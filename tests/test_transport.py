import hashlib
import hmac
import http.client
import json
import urllib.error

from ides_device_connector.transport import EventTransport
from ides_device_connector.cli import validate_ingest_url


def test_ingest_url_requires_https(monkeypatch):
    monkeypatch.delenv("ATTENDANCE_ALLOW_INSECURE_HTTP", raising=False)
    assert validate_ingest_url("https://school.example/events") == "https://school.example/events"
    try:
        validate_ingest_url("http://school.example/events")
    except RuntimeError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("expected insecure remote HTTP to be rejected")
    for malformed in ("https://:443/events", "https://user:password@school.example/events"):
        try:
            validate_ingest_url(malformed)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected malformed or credential-bearing URL to be rejected")


def test_ingest_url_allows_explicit_local_http_for_testing(monkeypatch):
    monkeypatch.setenv("ATTENDANCE_ALLOW_INSECURE_HTTP", "true")
    url = "http://127.0.0.1:3200/api/attendance/device-events"
    assert validate_ingest_url(url) == url


def test_transport_signs_exact_body(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"accepted":1}'

    def fake_urlopen(request, timeout):
        captured["body"] = request.data
        captured["headers"] = request.headers
        assert timeout == 30
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    EventTransport("https://example.test/events", "secret", "machine-1").send([{"eventId": "1"}])
    timestamp = captured["headers"].get("X-Attendance-Timestamp") or captured["headers"].get("X-attendance-timestamp")
    expected = hmac.new(b"secret", timestamp.encode() + b"." + captured["body"], hashlib.sha256).hexdigest()
    signature = captured["headers"].get("X-Attendance-Signature") or captured["headers"].get("X-attendance-signature")
    assert signature == f"sha256={expected}"
    assert json.loads(captured["body"]) == {"events": [{"eventId": "1"}]}


def test_transport_uses_device_token_without_shared_secret(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"accepted":1}'

    def fake_urlopen(request, timeout):
        captured["headers"] = request.headers
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    EventTransport("https://example.test/events", "", "machine-1", token="ides_dev_token").send([{"eventId": "1"}])
    assert captured["headers"].get("X-device-token") == "ides_dev_token"
    assert captured["headers"].get("X-attendance-signature") is None


def test_transport_retries_transient_http_failures(monkeypatch):
    attempts = 0
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"accepted":1}'

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    result = EventTransport("https://example.test/events", "", "machine-1", token="ides_dev_token", max_retries=2, retry_backoff_seconds=1).send([{"eventId": "1"}])

    assert result == {"accepted": 1}
    assert attempts == 3
    assert sleeps == [1, 2]


def test_transport_retries_dropped_http_connections(monkeypatch):
    attempts = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"accepted":1}'

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise http.client.RemoteDisconnected("peer closed connection")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)

    result = EventTransport(
        "https://example.test/events", "", "machine-1", token="ides_dev_token", max_retries=1
    ).send([{"eventId": "1"}])

    assert result == {"accepted": 1}
    assert attempts == 2


def test_transport_does_not_retry_permanent_http_failures(monkeypatch):
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        EventTransport("https://example.test/events", "", "machine-1", token="ides_dev_token", max_retries=3).send([{"eventId": "1"}])
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("expected HTTPError")
    assert attempts == 1


def test_transport_heartbeat_uses_device_auth(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"status":"online"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["headers"] = request.headers
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = EventTransport("https://example.test/api/attendance/device-events", "", "machine-1", token="ides_dev_token").heartbeat()

    assert result == {"status": "online"}
    assert captured["url"].endswith("/heartbeat")
    assert captured["body"] == b""
    assert captured["headers"].get("X-device-token") == "ides_dev_token"
