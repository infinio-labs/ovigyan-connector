import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from ovigyan_connector import tray
from ovigyan_connector.state import StateStore

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def note(connector="ok", devices=(), at=NOW, poll=60):
    return {"at": at.isoformat().replace("+00:00", "Z"), "connector": connector, "poll_seconds": poll, "devices": list(devices)}


def device(state):
    return {"serial": "SN", "state": state}


def level(n, now=NOW):
    return tray.summarize(n, now.timestamp())[0]


def test_no_note_means_not_running():
    assert tray.summarize(None) == ("unknown", "Ovigyan Connector: not running")


@pytest.mark.parametrize(
    "n, expected",
    [
        (note("unpaired"), "warn"),
        (note("revoked"), "bad"),
        (note("offline"), "warn"),
        (note("ok", [device("ok")]), "ok"),
        (note("ok", [device("ok"), device("ok")]), "ok"),
        (note("ok"), "warn"),  # connected but nothing added yet
        (note("ok", [device("pending")]), "warn"),
        (note("ok", [device("ok"), device("unreachable")]), "bad"),  # one bad terminal is enough
        (note("ok", [device("pending"), device("unreachable")]), "bad"),  # trouble outranks waiting
        (note("ok", [device("disabled"), device("rejected")]), "ok"),  # the school's own decisions are not faults
    ],
)
def test_the_icon_level_follows_the_most_important_problem(n, expected):
    assert level(n) == expected


def test_a_note_that_stopped_updating_means_the_connector_died():
    old = NOW - timedelta(seconds=tray.STALE_AFTER_SECONDS + 61)
    assert tray.summarize(note("ok", [device("ok")], at=old), NOW.timestamp()) == ("bad", "Ovigyan Connector: stopped responding")
    fresh = NOW - timedelta(seconds=tray.STALE_AFTER_SECONDS)
    assert level(note("ok", [device("ok")], at=fresh)) == "ok"


def test_a_longer_poll_interval_allows_a_longer_silence():
    quiet = NOW - timedelta(seconds=tray.STALE_AFTER_SECONDS + 100)
    assert level(note("ok", [device("ok")], at=quiet, poll=300)) == "ok"


def test_a_note_with_a_bad_timestamp_is_not_treated_as_stale():
    broken = note("ok", [device("ok")])
    broken["at"] = "yesterday-ish"
    assert level(broken) == "ok"


def test_the_text_counts_terminals_in_plain_words():
    assert "can't reach 2 terminals" in tray.summarize(note("ok", [device("unreachable")] * 2), NOW.timestamp())[1]
    assert "1 terminal waiting for approval" in tray.summarize(note("ok", [device("pending")]), NOW.timestamp())[1]


def test_reading_the_note_from_disk(tmp_path):
    store = StateStore(tmp_path)
    assert tray.read_note(store) is None
    store.status_path.write_text(json.dumps(note("ok")))
    assert tray.read_note(store)["connector"] == "ok"
    store.status_path.write_text("{not json")
    assert tray.read_note(store) is None
    store.status_path.write_text("[1]")
    assert tray.read_note(store) is None


def test_icons_are_drawn_in_the_colour_of_their_level():
    pytest.importorskip("PIL")
    for name, colour in tray.COLOURS.items():
        image = tray.make_icon(name)
        assert image.size == (64, 64) and image.getpixel((8, 32))[:3] == colour
    assert tray.make_icon("nonsense").getpixel((8, 32))[:3] == tray.COLOURS["unknown"]


def test_without_the_optional_packages_the_tray_says_what_to_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pystray", None)  # makes `import pystray` fail
    assert tray.run_tray(StateStore(tmp_path)) == 1
    assert "pystray" in capsys.readouterr().err


def test_the_menu_is_dashboard_status_and_exit(tmp_path, monkeypatch):
    """Right-click: Dashboard (also the double-click), what the connector is doing, and Exit. No other entries."""
    seen = {}

    class Menu:
        SEPARATOR = "---"

        def __init__(self, *items):
            seen["items"] = items

    class MenuItem:
        def __init__(self, text, action, default=False, enabled=True):
            self.text, self.action, self.default, self.enabled = text, action, default, enabled

    class Icon:
        def __init__(self, name, image, title, menu):
            seen["icon"] = self

        def run(self, setup=None):
            pass  # no event loop in a test

        def stop(self):
            seen["stopped"] = True

    monkeypatch.setitem(sys.modules, "pystray", type(sys)("pystray"))
    sys.modules["pystray"].Menu, sys.modules["pystray"].MenuItem, sys.modules["pystray"].Icon = Menu, MenuItem, Icon
    monkeypatch.setattr(tray, "make_icon", lambda *args, **kwargs: object())  # drawing needs Pillow; not under test
    assert tray.run_tray(StateStore(tmp_path)) == 0

    dashboard, status, line, exit_item = seen["items"]
    assert (dashboard.text, dashboard.default) == ("Dashboard", True)
    assert status.enabled is False and "Ovigyan" in status.text(None)
    assert line == Menu.SEPARATOR
    assert exit_item.text == "Exit"
    exit_item.action(seen["icon"])
    assert seen["stopped"]


def test_a_second_icon_does_not_start_while_the_first_is_showing(tmp_path):
    lock = tmp_path / "tray.lock"
    first = tray.acquire_single_instance(lock)
    assert first is not None
    assert tray.acquire_single_instance(lock) is None  # an icon already holds it
    first.close()  # the lock goes when the first icon's process ends
    again = tray.acquire_single_instance(lock)
    assert again is not None
    again.close()


def test_the_tray_command_exits_quietly_when_an_icon_is_already_showing(monkeypatch):
    monkeypatch.setattr(tray, "acquire_single_instance", lambda: None)
    monkeypatch.setitem(sys.modules, "pystray", None)  # it must not even try to draw an icon
    assert tray.run_tray() == 0
