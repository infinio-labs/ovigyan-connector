from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    device_user_id: str
    occurred_at: str
    direction: str
    verify_mode: str
    raw: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "deviceUserId": self.device_user_id,
            "timestamp": self.occurred_at,
            "punch": {"in": 0, "out": 1}.get(self.direction, 255),
            "verify": {"password": 0, "fingerprint": 1, "card": 2, "face": 15}.get(self.verify_mode, -1),
            "raw": self.raw,
        }


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _direction(punch: Any) -> str:
    # Only the two states every ZK firmware agrees on. 2/3 are break-out/break-in and 4/5 overtime
    # in/out: they are not a shift boundary, so they are kept raw as "unknown" instead of guessed
    # into in/out (a break-in read as "out" would put a lunch break in as the day's check-out).
    try:
        return {0: "in", 1: "out"}[int(punch)]
    except (KeyError, TypeError, ValueError):
        return "unknown"


def _verify_mode(value: Any) -> str:
    try:
        return {0: "password", 1: "fingerprint", 2: "card", 15: "face"}.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _timestamp(value: Any, timezone_name: str) -> str:
    if isinstance(value, datetime):
        current = value
    else:
        current = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(timezone_name))
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_zk_row(row: Any, timezone_name: str) -> EventEnvelope:
    user_id = _text(getattr(row, "user_id", None))
    timestamp = getattr(row, "timestamp", None)
    if not user_id or timestamp is None:
        raise ValueError("ZK attendance row has no user ID or timestamp")
    occurred_at = _timestamp(timestamp, timezone_name)
    punch = getattr(row, "punch", None)
    verify = getattr(row, "status", None)
    # Never use the ZK `uid`: on the terminal it is the user's slot, repeated on every punch, so it
    # would make a user's later punches look like duplicates of the first.
    event_id = hashlib.sha256(f"{user_id}|{occurred_at}|{punch}|{verify}".encode()).hexdigest()[:32]
    raw = {
        "uid": getattr(row, "uid", None),
        "user_id": user_id,
        "timestamp": occurred_at,
        "punch": punch,
        "status": verify,
    }
    return EventEnvelope(event_id, user_id, occurred_at, _direction(punch), _verify_mode(verify), raw)


def validate_event(event: Mapping[str, Any]) -> None:
    if not _text(event.get("eventId")) or not _text(event.get("deviceUserId")):
        raise ValueError("eventId and deviceUserId are required")
    _timestamp(event.get("timestamp"), "UTC")
