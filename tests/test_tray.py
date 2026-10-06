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
