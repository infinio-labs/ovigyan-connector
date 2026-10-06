import http.client
import json
import re

import pytest

from fake_cloud import CREDENTIAL, PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore
from ovigyan_connector.ui import COOKIE, UiServer, ui_token


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


@pytest.fixture
def ui(tmp_path):
    service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0)
    service.enable_wake()
    server = UiServer(service, port=0)
    server.start()
    yield server
    server.shutdown()
    server.server_close()


def call(server, method, path, body=None, *, cookie=True, host=None, origin=None, content_type="application/json", raw=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{server.port}"}
    if cookie:
        headers["Cookie"] = f"{COOKIE}={server.token}"
    if origin:
        headers["Origin"] = origin
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["Content-Type"] = content_type
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response, payload


def jcall(server, *args, **kwargs):
    response, payload = call(server, *args, **kwargs)
    return response.status, json.loads(payload or b"{}")


class TestAccess:
    def test_the_page_needs_the_secret_link(self, ui):
        response, body = call(ui, "GET", "/", cookie=False)
        assert response.status == 401 and b"ovigyan-connector open" in body

    def test_the_open_link_sets_a_strict_http_only_cookie_and_redirects(self, ui):
        response, _ = call(ui, "GET", f"/?t={ui.token}", cookie=False)
        assert response.status == 303 and response.getheader("location") == "/"
        cookie = response.getheader("set-cookie")
        assert f"{COOKIE}={ui.token}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie

    def test_a_wrong_secret_is_refused(self, ui):
        assert call(ui, "GET", "/?t=wrong", cookie=False)[0].status == 401
        response, _ = call(ui, "GET", "/api/status", cookie=False)
        assert response.status == 401

    def test_with_the_cookie_the_page_loads_with_a_nonce_only_csp(self, ui):
        response, body = call(ui, "GET", "/")
        assert response.status == 200 and b"Ovigyan Connector" in body
        nonce = re.search(rb'<script nonce="([^"]+)"', body).group(1).decode()
        csp = response.getheader("content-security-policy")
        assert f"script-src 'nonce-{nonce}'" in csp and "default-src 'none'" in csp and "unsafe-inline" not in csp
        assert response.getheader("x-frame-options") == "DENY" and response.getheader("cache-control") == "no-store"
        assert "__NONCE__" not in body.decode()

    def test_every_page_load_gets_a_fresh_nonce(self, ui):
        nonces = {re.search(rb'<script nonce="([^"]+)"', call(ui, "GET", "/")[1]).group(1) for _ in range(5)}
        assert len(nonces) == 5

    @pytest.mark.parametrize("host", ["evil.example.com", "127.0.0.1", "127.0.0.1:1", "localhost.evil.com:80", ""])
    def test_requests_for_other_hostnames_are_refused_even_with_the_cookie(self, ui, host):
        # defeats DNS rebinding: a hostile site pointed at 127.0.0.1 still sends its own Host header
        assert call(ui, "GET", "/api/status", host=host or "x")[0].status == 403
        assert call(ui, "GET", f"/?t={ui.token}", host=host or "x", cookie=False)[0].status == 403

    def test_localhost_is_accepted_as_a_hostname(self, ui):
        assert call(ui, "GET", "/api/status", host=f"localhost:{ui.port}")[0].status == 200

    def test_cross_origin_changes_are_refused_same_origin_allowed(self, ui):
        assert call(ui, "POST", "/api/unpair", {}, origin="https://evil.example.com")[0].status == 403
        assert call(ui, "POST", "/api/unpair", {}, origin=f"http://127.0.0.1:{ui.port}")[0].status == 200

    def test_changes_must_be_json(self, ui):
        assert jcall(ui, "POST", "/api/pair", raw=b"server=x", content_type="application/x-www-form-urlencoded")[0] == 415
        assert jcall(ui, "POST", "/api/pair", raw=b"text", content_type="text/plain")[0] == 415

    def test_bad_bodies_are_rejected(self, ui):
        assert jcall(ui, "POST", "/api/pair", raw=b"not json")[0] == 400
        assert jcall(ui, "POST", "/api/pair", raw=b"[1]")[0] == 400
        assert jcall(ui, "POST", "/api/pair", raw=b"x" * 9000)[0] == 413

    def test_unknown_routes_are_404(self, ui):
        assert jcall(ui, "GET", "/api/nothing")[0] == 404
        assert jcall(ui, "POST", "/api/nothing", {})[0] == 404
        assert jcall(ui, "DELETE", "/api/other/1")[0] == 404

    def test_the_secret_is_stable_across_restarts_and_owner_only(self, tmp_path):
        import os
        import stat
        service = ConnectorService(StateStore(tmp_path))
        first = ui_token(service)
        assert ui_token(service) == first and len(first) >= 32
        if os.name == "posix":
            assert stat.S_IMODE((tmp_path / "ui-token").stat().st_mode) == 0o600

    def test_it_listens_on_loopback_only(self, ui):
        assert ui.server_address[0] == "127.0.0.1"


class TestSetupFlow:
    def test_status_before_pairing(self, ui):
        status, body = jcall(ui, "GET", "/api/status")
        assert status == 200 and body["paired"] is False and body["devices"] == []

    def test_pairing_from_the_page(self, ui, cloud):
        status, body = jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": PAIR_KEY})
        assert status == 200 and body["connectorName"] == "Front office PC"
        assert jcall(ui, "GET", "/api/status")[1]["paired"] is True

    def test_a_wrong_key_shows_the_plain_message(self, ui, cloud):
        status, body = jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": "OVG-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ"})
        assert (status, body["error"], body["kind"]) == (400, "That key is not valid.", "invalid_key")

    def test_a_bad_address_shows_the_plain_message(self, ui):
        status, body = jcall(ui, "POST", "/api/pair", {"server": "", "key": PAIR_KEY})
        assert status == 400 and body["kind"] == "bad_address"

    def test_adding_a_terminal_from_the_page(self, ui, cloud):
        jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": PAIR_KEY})
        with FakeZkDevice([], serial="SN9", model="X2008") as zk:
            status, body = jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": str(zk.port), "password": "0"})
        assert status == 200 and body["device"]["serial"] == "SN9" and body["existing"] is False
        assert "SN9" in cloud.reported
        assert [d["serial"] for d in jcall(ui, "GET", "/api/status")[1]["devices"]] == ["SN9"]

    def test_adding_with_defaults_blank_uses_port_4370_and_no_password(self, ui, cloud):
        status, body = jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": "", "password": ""})
        assert status == 400 and "4370" in body["error"]  # tried 4370 (nothing there) and explained it

    def test_an_unreachable_terminal_is_explained(self, ui):
        status, body = jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": "1"})
        assert (status, body["kind"]) == (400, "unreachable") and "same network" in body["error"]

    def test_non_numeric_port_or_password_is_explained(self, ui):
        status, body = jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": "abc"})
        assert status == 400 and "must be numbers" in body["error"]

    def test_removing_a_terminal(self, ui, cloud):
        with FakeZkDevice([], serial="SN9") as zk:
            device_id = jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": str(zk.port)})[1]["device"]["id"]
        assert jcall(ui, "DELETE", f"/api/devices/{device_id}")[0] == 200
        assert jcall(ui, "DELETE", f"/api/devices/{device_id}")[0] == 404

    def test_disconnecting(self, ui, cloud):
        jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": PAIR_KEY})
        assert jcall(ui, "POST", "/api/unpair", {})[0] == 200
        assert jcall(ui, "GET", "/api/status")[1]["paired"] is False

    def test_status_never_contains_secrets(self, ui, cloud):
        jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": PAIR_KEY})
        with FakeZkDevice([], serial="SN9") as zk:
            jcall(ui, "POST", "/api/devices", {"host": "127.0.0.1", "port": str(zk.port), "password": "12345"})
        text = json.dumps(jcall(ui, "GET", "/api/status")[1])
        assert CREDENTIAL not in text and "12345" not in text and "password" not in text.lower()

    def test_a_crash_inside_an_action_does_not_leak_details(self, ui, monkeypatch):
        monkeypatch.setattr(ui.service, "pair", lambda *a: (_ for _ in ()).throw(RuntimeError("secret internals")))
        status, body = jcall(ui, "POST", "/api/pair", {"server": "https://x.test", "key": "k"})
        assert status == 500 and "secret internals" not in json.dumps(body) and "Check the connector" in body["error"]

    def test_changes_ask_the_service_to_run_a_cycle_now(self, ui, cloud):
        ui.service._wake.clear()
        jcall(ui, "POST", "/api/pair", {"server": cloud.url, "key": PAIR_KEY})
        assert ui.service._wake.is_set()
        ui.service._wake.clear()
        jcall(ui, "POST", "/api/refresh", {})
        assert ui.service._wake.is_set()


class TestWake:
    def test_the_loop_wakes_early_when_asked(self, tmp_path, cloud):
        import threading
        import time

        service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0)
        service.pair(cloud.url, PAIR_KEY)
        service.enable_wake()
        cloud.poll_seconds = 60
        stop = threading.Event()
        cycles = []
        original = service.run_cycle
        service.run_cycle = lambda: (cycles.append(1), original())[1]  # type: ignore[method-assign]
        thread = threading.Thread(target=service.run_forever, args=(stop,), daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not cycles and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        service.request_cycle()  # without this the next cycle is 60 s away
        while len(cycles) < 2 and time.time() < deadline:
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=5)
        assert len(cycles) >= 2 and not thread.is_alive()

    def test_stopping_ends_the_pause_promptly(self, tmp_path, cloud):
        import threading
        import time

        service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0)
        service.pair(cloud.url, PAIR_KEY)
        service.enable_wake()
        stop = threading.Event()
        thread = threading.Thread(target=service.run_forever, args=(stop,), daemon=True)
        thread.start()
        time.sleep(1)
        started = time.time()
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive() and time.time() - started < 2


class TestPage:
    """Guards for mistakes a browser would catch but a server test would not."""

    def test_the_page_has_no_inline_style_attributes_which_the_csp_would_block(self):
        from ovigyan_connector.ui_page import PAGE

        assert 'style="' not in PAGE and "style='" not in PAGE

    def test_hidden_elements_stay_hidden_whatever_their_display_rule(self):
        from ovigyan_connector.ui_page import PAGE

        assert "[hidden]{display:none!important}" in PAGE

    def test_the_page_loads_nothing_from_outside(self):
        from ovigyan_connector.ui_page import PAGE

        assert not re.search(r'(src|href)=["\']https?://', PAGE) and "@import" not in PAGE
