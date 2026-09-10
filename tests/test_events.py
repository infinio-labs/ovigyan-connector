from datetime import datetime
from types import SimpleNamespace

from ides_device_connector.events import from_zk_row


def test_x2008_row_preserves_unknown_punch_and_converts_device_timezone():
    event = from_zk_row(
        SimpleNamespace(uid=11, user_id="42", timestamp=datetime(2026, 9, 10, 9, 15), punch=255, status=1),
        "Asia/Kolkata",
    )
    assert event.event_id == "11"
    assert event.occurred_at == "2026-09-10T03:45:00.000Z"
    assert event.direction == "unknown"
    assert event.verify_mode == "fingerprint"
    assert event.raw["punch"] == 255


def test_event_id_falls_back_to_content_hash():
    row = SimpleNamespace(uid=None, user_id="42", timestamp=datetime(2026, 9, 10, 9, 15), punch=0, status=1)
    assert from_zk_row(row, "Asia/Kolkata").event_id == from_zk_row(row, "Asia/Kolkata").event_id
