import base64
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fake_cloud import PAIR_KEY, FakeCloud
from ovigyan_connector.branding import MAX_LOGO_BYTES, contrast_text, fetch_logo, is_small_png, parse_branding
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore
from ovigyan_connector.tray import make_icon, summarize
from test_ui import call, jcall  # noqa: F401  (helpers)


def png(width=8, height=8):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\x16\xa3\x4a\xff" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class TestParse:
    def test_keeps_good_fields_and_trims_the_name(self):
        assert parse_branding({"name": "  Greenfield   School ", "color": "#16A34A", "logoUrl": "https://x.test/l.png"}) == {
            "name": "Greenfield School", "color": "#16a34a", "logo_url": "https://x.test/l.png",
        }

    @pytest.mark.parametrize("raw", [None, [], "x", 5, {}, {"name": "  ", "color": None, "logoUrl": None}])
    def test_nothing_usable_is_empty(self, raw):
        assert parse_branding(raw) == {}

    @pytest.mark.parametrize("color", ["red", "#123", "#12345g", "rgb(0,0,0)", "url(x)", 7])
    def test_a_bad_colour_is_dropped(self, color):
        assert "color" not in parse_branding({"color": color})

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:image/png;base64,AA", "ftp://x/y.png", "//x/y.png"])
    def test_only_http_and_https_logo_addresses_are_kept(self, url):
        assert "logo_url" not in parse_branding({"logoUrl": url})

    def test_the_name_is_capped(self):
        assert len(parse_branding({"name": "x" * 500})["name"]) == 80


def test_text_colour_is_readable_on_the_brand_colour():
    assert contrast_text("#ffffff") == "#000000" and contrast_text("#000000") == "#ffffff"
    assert contrast_text("#2563eb") == "#ffffff" and contrast_text("#fde047") == "#000000"


class TestPngCheck:
    def test_accepts_a_small_png(self):
        assert is_small_png(png())

    @pytest.mark.parametrize(
        "data",
        [b"", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a" + b"0" * 30, png(2000, 2000), png()[:20], png(1, 1) + b"0" * MAX_LOGO_BYTES],
        ids=["empty", "svg", "gif", "too-big-side", "truncated", "too-many-bytes"],
    )
    def test_refuses_everything_else(self, data):
        assert not is_small_png(data)


class _Logo(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):  # noqa: N802
        self.server.hits += 1
        body = self.server.body
        self.send_response(self.server.status)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def logo_server():
    server = HTTPServer(("127.0.0.1", 0), _Logo)
    server.hits, server.body, server.status = 0, png(), 200
    server.url = f"http://127.0.0.1:{server.server_address[1]}/logo.png"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


class TestFetchLogo:
    def test_returns_base64_of_a_good_png(self, logo_server):
        assert base64.b64decode(fetch_logo(logo_server.url)) == png()

    def test_a_non_png_a_404_and_an_unreachable_host_all_give_none(self, logo_server):
        logo_server.body = b"<svg/>"
        assert fetch_logo(logo_server.url) is None
        logo_server.body, logo_server.status = png(), 404
        assert fetch_logo(logo_server.url) is None
        assert fetch_logo("http://127.0.0.1:1/x.png", timeout=1) is None


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def service_for(tmp_path, fetcher=None):
    return ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, logo_fetcher=fetcher or (lambda url: "LOGO:" + url))


class TestServiceAdoptsBranding:
    def test_pairing_stores_the_schools_look(self, tmp_path, cloud):
        cloud.branding = {"name": "Greenfield School", "color": "#16a34a", "logoUrl": "https://cdn.test/l.png"}
        state = service_for(tmp_path).pair(cloud.url, PAIR_KEY)
        assert state.branding == {"name": "Greenfield School", "color": "#16a34a", "logo_url": "https://cdn.test/l.png", "logo_png": "LOGO:https://cdn.test/l.png"}
        assert StateStore(tmp_path).load().branding == state.branding

    def test_a_site_without_branding_keeps_the_ovigyan_look(self, tmp_path, cloud):
        assert service_for(tmp_path).pair(cloud.url, PAIR_KEY).branding == {}

    def test_changes_on_the_site_reach_the_connector_on_the_next_cycle(self, tmp_path, cloud):
        service = service_for(tmp_path)
        cloud.branding = {"name": "Old Name", "color": "#111111"}
        service.pair(cloud.url, PAIR_KEY)
        cloud.branding = {"name": "New Name", "color": "#222222"}
        service.run_cycle()
        assert StateStore(tmp_path).load().branding == {"name": "New Name", "color": "#222222"}

    def test_an_empty_block_returns_to_the_defaults_but_a_missing_block_changes_nothing(self, tmp_path, cloud):
        service = service_for(tmp_path)
        cloud.branding = {"name": "Greenfield"}
        service.pair(cloud.url, PAIR_KEY)
        cloud.branding = None  # an older site
        service.run_cycle()
        assert StateStore(tmp_path).load().branding == {"name": "Greenfield"}
        cloud.branding = {}  # the site now sets nothing
        service.run_cycle()
        assert StateStore(tmp_path).load().branding == {}

    def test_the_logo_is_downloaded_once_per_address_and_retried_after_a_failure(self, tmp_path, cloud):
        calls, answer = [], [None]
        service = service_for(tmp_path, lambda url: calls.append(url) or answer[0])
        cloud.branding = {"logoUrl": "https://cdn.test/a.png"}
        service.pair(cloud.url, PAIR_KEY)
        assert "logo_png" not in StateStore(tmp_path).load().branding
        answer[0] = "PNG-A"
        service.run_cycle()
        service.run_cycle()
        assert calls == ["https://cdn.test/a.png"] * 2 and StateStore(tmp_path).load().branding["logo_png"] == "PNG-A"
        cloud.branding = {"logoUrl": "https://cdn.test/b.png"}
        answer[0] = "PNG-B"
        service.run_cycle()
        assert calls[-1] == "https://cdn.test/b.png" and StateStore(tmp_path).load().branding["logo_png"] == "PNG-B"

    def test_unpairing_returns_to_the_defaults(self, tmp_path, cloud):
        service = service_for(tmp_path)
        cloud.branding = {"name": "Greenfield"}
        service.pair(cloud.url, PAIR_KEY)
        assert service.unpair().branding == {}

    def test_the_status_note_carries_the_look_for_the_tray_and_the_page_summary_for_the_ui(self, tmp_path, cloud):
        service = service_for(tmp_path)
        cloud.branding = {"name": "Greenfield", "color": "#16a34a", "logoUrl": "https://cdn.test/l.png"}
        service.pair(cloud.url, PAIR_KEY)
        service.run_cycle()
        note = json.loads(StateStore(tmp_path).status_path.read_text())
        assert note["brand"] == {"name": "Greenfield", "color": "#16a34a", "logo_png": "LOGO:https://cdn.test/l.png"}
        summary = service.status()["brand"]
        assert (summary["name"], summary["color"], summary["text"]) == ("Greenfield", "#16a34a", "#000000") and summary["logo"]
        assert "logo_png" not in json.dumps(service.status())


class TestUi:
    @pytest.fixture
    def ui(self, tmp_path):
        from ovigyan_connector.ui import UiServer

        service = service_for(tmp_path)
        service.store.save(service.store.load())
        server = UiServer(service, port=0)
        server.start()
        yield server, service
        server.shutdown()
        server.server_close()

    def test_the_logo_is_served_only_with_the_cookie_and_only_when_there_is_one(self, ui):
        server, service = ui
        assert call(server, "GET", "/logo.png")[0].status == 404
        state = service.store.load()
        state.branding = {"logo_png": base64.b64encode(png()).decode()}
        service.store.save(state)
        response, body = call(server, "GET", "/logo.png")
        assert (response.status, response.getheader("content-type"), body) == (200, "image/png", png())
        assert call(server, "GET", "/logo.png", cookie=False)[0].status == 404

    def test_the_page_may_load_its_own_logo_and_nothing_else(self, ui):
        server, _ = ui
        response, _ = call(server, "GET", "/")
        assert "img-src 'self'" in response.getheader("content-security-policy")


class TestTray:
    def test_the_tooltip_uses_the_schools_name(self):
        note = {"connector": "ok", "devices": [{"state": "ok"}], "brand": {"name": "Greenfield"}}
        assert summarize(note) == ("ok", "Greenfield: working")
        assert summarize({"connector": "ok", "devices": [{"state": "ok"}]})[1] == "Ovigyan Connector: working"

    def test_the_icon_shows_the_logo_with_a_status_dot_and_survives_a_bad_logo(self):
        pytest.importorskip("PIL")
        icon = make_icon("ok", logo_png=base64.b64encode(png()).decode())
        assert icon.size == (64, 64) and icon.getpixel((10, 10))[:3] == (0x16, 0xA3, 0x4A)
        assert icon.getpixel((58, 58))[:3] == (26, 127, 55)  # the status dot
        assert make_icon("ok", logo_png="not-a-png").getpixel((32, 4))[3] == 255  # fell back to the plain disc
