"""Real EsslX2008Adapter (pyzk) -> fake terminal over TCP -> CLI outbox -> stub cloud over HTTP."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fake_zk_device import FakeZkDevice
from ovigyan_connector import cli
from ovigyan_connector.adapters import EsslX2008Adapter

ROWS = [
    {"uid": 1, "user_id": "42", "timestamp": "2026-09-10T08:00:00", "punch": 0, "status": 1},
    {"uid": 2, "user_id": "42", "timestamp": "2026-09-10T16:00:00", "punch": 1, "status": 15},
    {"uid": 3, "user_id": "7", "timestamp": "2026-09-10T08:05:00", "punch": 4, "status": 2},
]


def adapter(port):
    return EsslX2008Adapter("127.0.0.1", port, 0, 5, "Asia/Kolkata")


def test_adapter_reads_punches_from_terminal():
    with FakeZkDevice(list(ROWS)) as device:
        events = adapter(device.port).read_events()
    assert [e.device_user_id for e in events] == ["42", "42", "7"]
    assert events[0].occurred_at == "2026-09-10T02:30:00.000Z"  # 08:00 IST
    assert [e.direction for e in events] == ["in", "out", "unknown"]
    assert [e.verify_mode for e in events] == ["fingerprint", "face", "card"]


def test_adapter_empty_log():
    with FakeZkDevice([]) as device:
        assert adapter(device.port).read_events() == []


class _Cloud(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        status = self.server.status
        self.server.requests.append((self.path, body, status))
        payload = json.dumps({"accepted": len(json.loads(body or b"{}").get("events", []))}).encode()
        self.send_response(status)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


@pytest.fixture
def cloud():
    server = HTTPServer(("127.0.0.1", 0), _Cloud)
    server.status, server.requests = 200, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def run_cli(monkeypatch, tmp_path, device_port, cloud):
    for key, value in {
        "ATTENDANCE_DEVICE_HOST": "127.0.0.1",
        "ATTENDANCE_DEVICE_PORT": str(device_port),
        "ATTENDANCE_MACHINE_ID": "SIM-1",
        "ATTENDANCE_DEVICE_TOKEN": "ovigyan_dev_x",
        "ATTENDANCE_INGEST_URL": f"http://127.0.0.1:{cloud.server_port}/api/attendance/device-events",
        "ATTENDANCE_ALLOW_INSECURE_HTTP": "true",
        "ATTENDANCE_OUTBOX_PATH": str(tmp_path / "outbox.db"),
        "ATTENDANCE_HTTP_RETRIES": "0",
    }.items():
        monkeypatch.setenv(key, value)
    return cli.run_once()


def test_cli_delivers_then_keeps_queue_when_cloud_rejects(monkeypatch, tmp_path, cloud):
    with FakeZkDevice(list(ROWS)) as device:
        assert run_cli(monkeypatch, tmp_path, device.port, cloud) == 0
        assert [p for p, *_ in cloud.requests] == [
            "/api/attendance/heartbeat",
            "/api/attendance/device-events",
        ]
        cloud.status = 409  # e.g. unmapped device user
        cloud.requests.clear()
        with pytest.raises(Exception):
            run_cli(monkeypatch, tmp_path, device.port, cloud)


def test_cli_keeps_only_deferred_events_and_does_not_resend_delivered(monkeypatch, tmp_path, cloud):
    # Cloud defers one event (unmapped user): it stays queued, the rest are acknowledged.
    with FakeZkDevice(list(ROWS)) as device:
        orig = _Cloud.do_POST

        def defer_first(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            events = json.loads(body or b"{}").get("events", [])
            self.server.requests.append((self.path, body, 200))
            held = [events[0]["eventId"]] if events and self.path.endswith("device-events") else []
            payload = json.dumps({"accepted": len(events) - len(held), "deferredEventIds": held}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        monkeypatch.setattr(_Cloud, "do_POST", defer_first)
        assert run_cli(monkeypatch, tmp_path, device.port, cloud) == 0
        posted = [json.loads(b)["events"] for p, b, _ in cloud.requests if p.endswith("device-events")]
        assert len(posted) == 1 and len(posted[0]) == 3
        cloud.requests.clear()
        assert run_cli(monkeypatch, tmp_path, device.port, cloud) == 0  # second poll re-reads all 3
        resent = [json.loads(b)["events"] for p, b, _ in cloud.requests if p.endswith("device-events")]
        assert len(resent) == 1 and len(resent[0]) == 1  # only the deferred one
        monkeypatch.setattr(_Cloud, "do_POST", orig)
