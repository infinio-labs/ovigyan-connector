import pytest

from fake_cloud import PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector.cli import main
from ovigyan_connector.state import StateStore


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def run(capsys, home, *args):
    code = main(["--home", str(home), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_whole_setup_from_the_command_line(tmp_path, cloud, capsys):
    with FakeZkDevice([{"uid": 1, "user_id": "7", "timestamp": "2026-10-06T09:00:00", "punch": 0, "status": 1}], serial="SN1", model="X2008") as zk:
        code, out, _ = run(capsys, tmp_path, "pair", "--server", cloud.url, "--key", PAIR_KEY)
        assert code == 0 and 'Paired as "Front office PC"' in out and "add-device" in out
        code, out, _ = run(capsys, tmp_path, "add-device", "--host", "127.0.0.1", "--port", str(zk.port))
        assert code == 0 and "Added X2008 SN1" in out and "approves it" in out
        code, out, _ = run(capsys, tmp_path, "run", "--once")
        assert code == 0 and "waiting for approval" in out
        cloud.approve("SN1")
        code, out, _ = run(capsys, tmp_path, "run", "--once")
        assert code == 0 and "working" in out and "sent 1" in out
        code, out, _ = run(capsys, tmp_path, "devices")
        assert code == 0 and "SN1" in out and "working" in out
        code, out, _ = run(capsys, tmp_path, "status")
        assert code == 0 and "Paired with" in out and "Front office PC" in out and "Last check" in out


def test_options_work_after_the_command_too(tmp_path, cloud, capsys):
    code = main(["status", "--home", str(tmp_path)])
    assert code == 0 and "Not paired" in capsys.readouterr().out


def test_a_wrong_key_prints_a_plain_message_and_fails(tmp_path, cloud, capsys):
    code, _, err = run(capsys, tmp_path, "pair", "--server", cloud.url, "--key", "OVG-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ")
    assert code == 1 and "That key is not valid." in err and "Traceback" not in err


def test_an_unreachable_terminal_prints_a_plain_message_and_fails(tmp_path, cloud, capsys):
    run(capsys, tmp_path, "pair", "--server", cloud.url, "--key", PAIR_KEY)
    code, _, err = run(capsys, tmp_path, "add-device", "--host", "127.0.0.1", "--port", "1")
    assert code == 1 and "Can't reach 127.0.0.1:1" in err
    assert StateStore(tmp_path).load().devices == []


def test_a_bad_address_is_explained(tmp_path, capsys):
    code, _, err = run(capsys, tmp_path, "add-device", "--host", "not a host")
    assert code == 1 and "IP address" in err


def test_running_unpaired_once_fails_with_the_reason(tmp_path, capsys):
    code, out, _ = run(capsys, tmp_path, "run", "--once")
    assert code == 1 and "unpaired" in out and "Not paired" in out


def test_remove_device_and_unpair(tmp_path, cloud, capsys):
    with FakeZkDevice([], serial="SN1") as zk:
        run(capsys, tmp_path, "pair", "--server", cloud.url, "--key", PAIR_KEY)
        run(capsys, tmp_path, "add-device", "--host", "127.0.0.1", "--port", str(zk.port))
    device_id = StateStore(tmp_path).load().devices[0].id
    assert run(capsys, tmp_path, "remove-device", device_id)[0] == 0
    code, _, err = run(capsys, tmp_path, "remove-device", device_id)
    assert code == 1 and "No terminal" in err
    code, out, _ = run(capsys, tmp_path, "unpair")
    assert code == 0 and "Disconnected" in out and not StateStore(tmp_path).load().paired


def test_the_original_environment_variable_mode_still_works(tmp_path, monkeypatch, capsys):
    # no subcommand: the pre-pairing behaviour (used by the web app's end-to-end tests) is untouched
    monkeypatch.setenv("ATTENDANCE_DEVICE_HOST", "127.0.0.1")
    monkeypatch.delenv("ATTENDANCE_MACHINE_ID", raising=False)
    assert main(["--once"]) == 1
    assert "ATTENDANCE_MACHINE_ID" in capsys.readouterr().err
