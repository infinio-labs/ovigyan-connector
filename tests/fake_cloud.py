"""A stand-in for the Ovigyan connector API, in memory, with the same shapes as the real endpoints."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAIR_KEY = "OVG-AAAAA-BBBBB-CCCCC-DDDDD"
CREDENTIAL = "ovigyan_conn_" + "a" * 64


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        pass

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        cloud: FakeCloud = self.server.cloud  # type: ignore[attr-defined]
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else {}
        cloud.requests.append({"method": method, "path": self.path, "headers": dict(self.headers), "body": body})
        if cloud.force_status:
            return self._send(cloud.force_status, {"error": cloud.force_error})
        if self.path == "/api/connectors/pair":
            key = str(body.get("key", ""))
            if key == "EXPIRED":
                return self._send(410, {"error": "This key has expired. Generate a new one in Settings."})
            if key != PAIR_KEY or cloud.key_used:
                return self._send(400, {"error": "That key is not valid."})
            cloud.key_used = True
            return self._send(200, {"connectorId": "c1", "name": "Front office PC", "credential": CREDENTIAL, "pollSeconds": cloud.poll_seconds, **cloud.brand_block()})
        if self.headers.get("authorization") != f"Bearer {CREDENTIAL}" or cloud.revoked:
            return self._send(401, {"error": "Unauthorized."})
        if self.path == "/api/connectors/config":
            return self._send(200, cloud.config())
        if self.path == "/api/connectors/devices":
            for device in body.get("devices", []):
                cloud.reported[device["serialNumber"]] = device
            return self._send(200, {"devices": [{"serialNumber": s, "state": cloud.state_of(s)} for s in cloud.reported]})
        if self.path == "/api/attendance/heartbeat":
            cloud.heartbeats.append(self.headers.get("x-device-machine-id"))
            return self._send(200, {"status": "online"})
        if self.path == "/api/attendance/device-events":
            machine = self.headers.get("x-device-machine-id", "")
            if cloud.state_of(machine) != "approved":
                return self._send(401, {"error": "Unknown or inactive device."})
            events = body.get("events", [])
            cloud.received.setdefault(machine, []).extend(events)
            return self._send(200, {"accepted": len(events), "submitted": len(events), "deferredEventIds": [], "unmappedUserIds": []})
        return self._send(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


class FakeCloud(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.cloud = self
        self.url = f"http://127.0.0.1:{self.server_address[1]}"
        self.requests: list[dict] = []
        self.reported: dict[str, dict] = {}
        self.approved: dict[str, dict] = {}
        self.rejected: set[str] = set()
        self.received: dict[str, list[dict]] = {}
        self.heartbeats: list[str | None] = []
        self.key_used = False
        self.revoked = False
        self.poll_seconds = 60
        self.force_status = 0
        self.force_error = "boom"
        self.branding: dict | None = None  # None = an older site that sends no branding block

    def approve(self, serial: str, ignore_before: str | None = None, enabled: bool = True, timezone: str = "Asia/Kolkata") -> None:
        self.approved[serial] = {
            "serialNumber": serial, "name": f"Device {serial}", "timezone": timezone,
            "ignoreBefore": ignore_before, "enabled": enabled,
        }

    def state_of(self, serial: str) -> str:
        return "approved" if serial in self.approved else "rejected" if serial in self.rejected else "pending"

    def config(self) -> dict:
        return {
            "pollSeconds": self.poll_seconds,
            "devices": list(self.approved.values()),
            "pending": [s for s in self.reported if self.state_of(s) == "pending"],
            "rejected": sorted(self.rejected),
            **self.brand_block(),
        }

    def brand_block(self) -> dict:
        return {} if self.branding is None else {"branding": self.branding}

    def __enter__(self) -> "FakeCloud":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
        self.server_close()
