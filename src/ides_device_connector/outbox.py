from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


class Outbox:
    """Small durable queue: enqueue is idempotent; delete follows HTTP success."""

    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.connection.commit()

    def enqueue(self, event_id: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO events(event_id, payload) VALUES (?, ?)",
            (event_id, json.dumps(payload, separators=(",", ":"), sort_keys=True)),
        )
        self.connection.commit()

    def pending(self, limit: int = 500) -> list[tuple[str, dict[str, Any]]]:
        rows = self.connection.execute(
            "SELECT event_id, payload FROM events ORDER BY created_at, event_id LIMIT ?", (limit,)
        ).fetchall()
        return [(event_id, json.loads(payload)) for event_id, payload in rows]

    def acknowledge(self, event_ids: Iterable[str]) -> None:
        self.connection.executemany("DELETE FROM events WHERE event_id = ?", ((event_id,) for event_id in event_ids))
        self.connection.commit()

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def close(self) -> None:
        self.connection.close()
