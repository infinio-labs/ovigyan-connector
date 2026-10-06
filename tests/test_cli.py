import pytest

from ovigyan_connector.cli import validate_test_ingest_url


def test_test_ingest_url_only_allows_local_http(monkeypatch):
    monkeypatch.setenv("ATTENDANCE_ALLOW_INSECURE_HTTP", "true")

    assert validate_test_ingest_url("http://127.0.0.1:3200/api/attendance/device-events") == (
        "http://127.0.0.1:3200/api/attendance/device-events"
    )

    with pytest.raises(RuntimeError, match="localhost"):
        validate_test_ingest_url("https://school.example.com/api/attendance/device-events")
