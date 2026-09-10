import hashlib
import hmac
import json

from ides_device_connector.transport import EventTransport


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
