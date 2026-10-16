import pytest

from fake_cloud import CREDENTIAL, PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector import __version__
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def service(tmp_path):
    return ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0)


def test_the_summary_says_what_is_installed_connected_and_how_each_terminal_is(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1", model="X2008") as zk:
        svc = service(tmp_path)
        svc.pair(cloud.url, PAIR_KEY)
        svc.add_device("127.0.0.1", zk.port)
        cloud.approve("SN1")
        svc.run_cycle()
        text = svc.support_info()
    assert f"Version: {__version__}" in text
    assert f'Connected to: {cloud.url} as "Front office PC"' in text
    assert "- X2008 SN1 at 127.0.0.1:" in text and "ok Working." in text
    assert "clock " in text  # the terminal's clock offset is there for whoever is helping


def test_an_unpaired_connector_with_no_terminals_still_gives_a_summary(tmp_path):
    text = service(tmp_path).support_info()
    assert "Connected to: nothing (unpaired)" in text and "Terminals: none added" in text


def test_the_end_of_the_log_is_included_and_every_secret_in_it_is_removed(tmp_path, cloud):
    svc = service(tmp_path)
    svc.pair(cloud.url, PAIR_KEY)
    (tmp_path / "connector.log").write_text(
        "\n".join(
            [
                "old line that falls off the end",
                f"2026-10-16 using credential {CREDENTIAL} for a request",
                "pairing with key OVG-AAAAA-BBBBB-CCCCC-DDDDD",
                "page link http://127.0.0.1:47890/?t=SECRETSECRETSECRET opened",
                "the terminal at 192.168.1.50 is unreachable",
            ]
        ),
        encoding="utf-8",
    )
    text = svc.support_info(log_lines=4)
    assert "old line that falls off the end" not in text
    assert "the terminal at 192.168.1.50 is unreachable" in text  # an address is useful, not a secret
    for secret in (CREDENTIAL, "OVG-AAAAA-BBBBB-CCCCC-DDDDD", "SECRETSECRETSECRET"):
        assert secret not in text
    assert text.count("[removed]") >= 3


def test_the_summary_never_holds_the_stored_credential_even_without_a_log(tmp_path, cloud):
    svc = service(tmp_path)
    svc.pair(cloud.url, PAIR_KEY)
    assert CREDENTIAL not in svc.support_info()
