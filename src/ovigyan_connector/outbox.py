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
        # Ledger of acknowledged events: the terminal log is never cleared, so every poll re-reads
        # all history; this keeps it from being queued and re-sent each time.
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS delivered (event_id TEXT PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        # Events the cloud permanently refused. Kept (with the reason) for inspection and so the
        # re-read terminal log does not queue them again; they no longer block delivery.
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS rejected "
            "(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, error TEXT NOT NULL, at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        # Pruned rows are only ever re-sent once more; the cloud dedupes by event ID.
        self.connection.execute("DELETE FROM delivered WHERE at < datetime('now', '-90 days')")
        self.connection.execute("DELETE FROM rejected WHERE at < datetime('now', '-90 days')")
        self.connection.commit()

    def enqueue(self, event_id: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO events(event_id, payload) SELECT ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM delivered WHERE event_id = ?) "
            "AND NOT EXISTS (SELECT 1 FROM rejected WHERE event_id = ?)",
            (event_id, json.dumps(payload, separators=(",", ":"), sort_keys=True), event_id, event_id),
        )
        self.connection.commit()

    def pending(self, limit: int = 500, exclude: Iterable[str] = ()) -> list[tuple[str, dict[str, Any]]]:
        skip = list(exclude)
        marks = ",".join("?" * len(skip))
        rows = self.connection.execute(
            f"SELECT event_id, payload FROM events {f'WHERE event_id NOT IN ({marks})' if skip else ''} "
            "ORDER BY created_at, event_id LIMIT ?",
            (*skip, limit),
        ).fetchall()
        return [(event_id, json.loads(payload)) for event_id, payload in rows]

    def acknowledge(self, event_ids: Iterable[str]) -> None:
        ids = [(event_id,) for event_id in event_ids]
        self.connection.executemany("INSERT OR IGNORE INTO delivered(event_id) VALUES (?)", ids)
        self.connection.executemany("DELETE FROM events WHERE event_id = ?", ids)
        self.connection.commit()

    def quarantine(self, event_id: str, error: str) -> None:
        """Move an event the cloud refuses out of the queue so it cannot block the ones behind it."""
        self.connection.execute(
            "INSERT OR REPLACE INTO rejected(event_id, payload, error) SELECT event_id, payload, ? FROM events WHERE event_id = ?",
            (error, event_id),
        )
        self.connection.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        self.connection.commit()

    def rejected_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM rejected").fetchone()[0])

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def close(self) -> None:
        self.connection.close()
