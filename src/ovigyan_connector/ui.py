"""The connector's local page: a small web server that only answers this PC.

It serves one page and a handful of JSON endpoints that call the same ConnectorService as the command
line. Because anything on the PC (or a web page in the user's browser) could try to reach 127.0.0.1, it:
binds to loopback only; checks the Host header (defeats DNS rebinding); requires a secret that only the
`ovigyan-connector open` link carries, kept in an HttpOnly SameSite=Strict cookie; refuses cross-origin
requests; and accepts changes only as JSON.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .cloud import CloudError
from .devices import ProbeError
from .service import ConnectorService
from .ui_page import PAGE

DEFAULT_UI_PORT = 47890
COOKIE = "ovigyan_connector"
MAX_BODY = 8 * 1024


def ui_token(service: ConnectorService) -> str:
    """The secret that opens the page. Created once, kept owner-only next to the connector's other secrets."""
    path = service.store.home / "ui-token"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    service.store.home.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    path.write_text(value, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return value


def open_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/?t={token}"


class _Handler(BaseHTTPRequestHandler):
    server: "UiServer"
    server_version = "OvigyanConnector"

    def log_message(self, *_: object) -> None:  # the console stays quiet; errors go to stderr below
        pass

    # ── helpers ────────────────────────────────────────────────────────────────────────────────────
    def _headers(self, status: int, content_type: str, length: int, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(length))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("x-frame-options", "DENY")
        self.send_header("referrer-policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self._headers(status, "application/json", len(data))
        self.wfile.write(data)

    def _text(self, status: int, text: str) -> None:
        data = text.encode()
        self._headers(status, "text/plain; charset=utf-8", len(data))
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("host", "") in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _authed(self) -> bool:
        cookies = {}
        for part in self.headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            cookies[name] = value
        return hmac.compare_digest(cookies.get(COOKIE, ""), self.server.token)

    def _guard(self, changes: bool) -> bool:
        """True when the request may proceed; otherwise the answer has been sent."""
        if not self._host_ok():
            self._text(403, "Forbidden.")
            return False
        if not self._authed():
            self._json(401, {"error": "Open the connector from its shortcut, or run: ovigyan-connector open"})
            return False
        if changes:
            origin = self.headers.get("origin")
            if origin and origin != f"http://{self.headers.get('host')}":
                self._text(403, "Forbidden.")
                return False
        return True

    def _read_json(self) -> dict | None:
        if self.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            self._json(415, {"error": "Send JSON."})
            return None
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._json(413, {"error": "That request is too large."})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._json(400, {"error": "Send a JSON object."})
            return None
        return body

    def _run(self, action) -> None:
        try:
            self._json(200, action())
        except (CloudError, ProbeError) as error:
            self._json(400, {"error": error.message, "kind": error.kind})
        except Exception as error:  # noqa: BLE001 - never leak internals to the page
            print(f"ovigyan-connector: ui: {error}", file=sys.stderr)
            self._json(500, {"error": "Something went wrong. Check the connector's log and try again."})

    # ── routes ─────────────────────────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._host_ok():
            return self._text(403, "Forbidden.")
        if url.path == "/" and "t" in parse_qs(url.query):
            # The link from `ovigyan-connector open`: trade the secret in the URL for a cookie, then drop it from the address bar.
            if hmac.compare_digest(parse_qs(url.query)["t"][0], self.server.token):
                self._headers(
                    303, "text/plain", 0,
                    {"location": "/", "set-cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"},
                )
                return
            return self._text(401, "That link is not valid. Run: ovigyan-connector open")
        if url.path == "/":
            if not self._authed():
                return self._text(
                    401,
                    "Open the Ovigyan Connector from its shortcut, or run:  ovigyan-connector open",
                )
            nonce = secrets.token_urlsafe(16)
            data = PAGE.replace("__NONCE__", nonce).encode()
            csp = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; form-action 'none'"
            self._headers(200, "text/html; charset=utf-8", len(data), {"content-security-policy": csp})
            self.wfile.write(data)
            return
        if url.path == "/api/status":
            if self._guard(False):
                self._run(self.server.service.status)
            return
        self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/pair", "/api/devices", "/api/unpair", "/api/refresh"}:
            return self._json(404, {"error": "Not found."})
        if not self._guard(True):
            return
        body = self._read_json()
        if body is None:
            return
        service = self.server.service

        def after(result: dict) -> dict:
            service.request_cycle()
            return result

        if path == "/api/pair":
            self._run(lambda: after(self._pair(service, body)))
        elif path == "/api/devices":
            self._run(lambda: after(self._add_device(service, body)))
        elif path == "/api/unpair":
            self._run(lambda: after(self._unpair(service)))
        else:
            self._run(lambda: after({"ok": True}))

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/devices/"):
            return self._json(404, {"error": "Not found."})
        if not self._guard(True):
            return
        device_id = path.removeprefix("/api/devices/")
        if not device_id or "/" in device_id:
            return self._json(404, {"error": "Not found."})
        if self.server.service.remove_device(device_id):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "No terminal with that id."})

    @staticmethod
    def _pair(service: ConnectorService, body: dict) -> dict:
        state = service.pair(str(body.get("server", "")), str(body.get("key", "")))
        return {"ok": True, "connectorName": state.connector_name, "server": state.server}

    @staticmethod
    def _add_device(service: ConnectorService, body: dict) -> dict:
        try:
            port = int(str(body.get("port") or 4370).strip())
            password = int(str(body.get("password") or 0).strip())
        except ValueError:
            raise ProbeError("bad_address", "The port and the communication password must be numbers.") from None
        device, existing = service.add_device(str(body.get("host", "")), port, password)
        return {"ok": True, "existing": existing, "device": {"id": device.id, "serial": device.serial, "model": device.model, "host": device.host, "port": device.port}}

    @staticmethod
    def _unpair(service: ConnectorService) -> dict:
        service.unpair()
        return {"ok": True}


class UiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: ConnectorService, port: int = DEFAULT_UI_PORT, token: str | None = None):
        super().__init__(("127.0.0.1", port), _Handler)
        self.service = service
        self.token = token or ui_token(service)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="ovigyan-ui", daemon=True)
        thread.start()
        return thread

    @property
    def port(self) -> int:
        return self.server_address[1]
