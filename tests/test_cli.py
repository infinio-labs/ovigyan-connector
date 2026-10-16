import pytest

from ovigyan_connector.cli import validate_test_ingest_url


def test_test_ingest_url_only_allows_local_http(monkeypatch):
    monkeypatch.setenv("ATTENDANCE_ALLOW_INSECURE_HTTP", "true")

    assert validate_test_ingest_url("http://127.0.0.1:3200/api/attendance/device-events") == (
        "http://127.0.0.1:3200/api/attendance/device-events"
    )

    with pytest.raises(RuntimeError, match="localhost"):
        validate_test_ingest_url("https://school.example.com/api/attendance/device-events")


LEGACY_VARIABLES = (
    "ATTENDANCE_DEVICE_HOST",
    "ATTENDANCE_MACHINE_ID",
    "ATTENDANCE_INGEST_URL",
    "ATTENDANCE_DEVICE_TOKEN",
    "ATTENDANCE_INGEST_SECRET",
)


def test_a_launch_with_nothing_configured_opens_the_setup_page(monkeypatch):
    from ovigyan_connector import cli, commands

    for name in LEGACY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    started = []
    monkeypatch.setattr(commands, "run_command", lambda argv: started.append(argv) or 0)

    assert cli.main([]) == 0
    assert started == [["run", "--open"]]


def test_the_old_environment_mode_still_runs_when_it_is_configured(monkeypatch):
    from ovigyan_connector import cli, commands

    monkeypatch.setenv("ATTENDANCE_DEVICE_HOST", "192.168.1.50")
    monkeypatch.setattr(commands, "run_command", lambda argv: pytest.fail("must not pair-and-run"))
    ran = []
    monkeypatch.setattr(cli, "run_once", lambda: ran.append(True))

    def stop(_seconds):
        raise SystemExit(0)

    monkeypatch.setattr(cli.time, "sleep", stop)
    with pytest.raises(SystemExit):
        cli.main([])
    assert ran == [True]


def test_the_missing_setting_error_points_to_the_normal_way_to_set_up(monkeypatch):
    from ovigyan_connector.cli import required

    monkeypatch.delenv("ATTENDANCE_DEVICE_HOST", raising=False)
    with pytest.raises(RuntimeError, match="ATTENDANCE_DEVICE_HOST.*ovigyan-connector run"):
        required("ATTENDANCE_DEVICE_HOST")
