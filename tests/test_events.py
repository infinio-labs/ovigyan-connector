from datetime import datetime
from types import SimpleNamespace

from ides_device_connector.events import from_zk_row


def test_x2008_row_preserves_unknown_punch_and_converts_device_timezone():
    event = from_zk_row(
        SimpleNamespace(uid=11, user_id="42", timestamp=datetime(2026, 9, 10, 9, 15), punch=255, status=1),
        "Asia/Kolkata",
    )
    assert len(event.event_id) == 32
    assert event.occurred_at == "2026-09-10T03:45:00.000Z"
    assert event.direction == "unknown"
    assert event.verify_mode == "fingerprint"
    assert event.raw["punch"] == 255


def test_event_id_falls_back_to_content_hash():
    row = SimpleNamespace(uid=None, user_id="42", timestamp=datetime(2026, 9, 10, 9, 15), punch=0, status=1)
    assert from_zk_row(row, "Asia/Kolkata").event_id == from_zk_row(row, "Asia/Kolkata").event_id


def test_same_user_slot_uid_does_not_collide_across_punches():
    # ZK reports the user's slot as uid on every punch; ids must still differ per punch.
    rows = [
        SimpleNamespace(uid=42, user_id="42", timestamp=datetime(2026, 9, 10, h), punch=p, status=1)
        for h, p in ((8, 0), (16, 1))
    ]
    assert from_zk_row(rows[0], "Asia/Kolkata").event_id != from_zk_row(rows[1], "Asia/Kolkata").event_id


def test_break_and_overtime_punches_are_not_guessed_into_in_or_out():
    from ides_device_connector.events import from_zk_row

    directions = {
        punch: from_zk_row(
            SimpleNamespace(uid=1, user_id="42", timestamp=datetime(2026, 9, 10, 9, 15), punch=punch, status=1),
            "Asia/Kolkata",
        ).direction
        for punch in range(0, 6)
    }
    assert directions == {0: "in", 1: "out", 2: "unknown", 3: "unknown", 4: "unknown", 5: "unknown"}
