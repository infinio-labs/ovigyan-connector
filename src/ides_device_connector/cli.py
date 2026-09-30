from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from .adapters import EsslX2008Adapter
from .events import from_zk_row
from .outbox import Outbox
from .transport import EventTransport


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def validate_ingest_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.password is None:
        return url
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and os.environ.get("ATTENDANCE_ALLOW_INSECURE_HTTP", "").strip().lower() == "true"
    ):
        return url
    raise RuntimeError(
        "ATTENDANCE_INGEST_URL must use HTTPS; insecure HTTP is allowed only for explicit localhost testing"
    )


def validate_test_ingest_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("--test-events may send only to a localhost HTTP ingest URL")
    return validate_ingest_url(url)


def load_test_events(path: str, timezone_name: str):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("--test-events file must contain a JSON array of ZK attendance rows")
    return [from_zk_row(SimpleNamespace(**row), timezone_name) for row in rows]


def run_once(test_events_path: str | None = None) -> int:
    host = "test replay" if test_events_path else required("ATTENDANCE_DEVICE_HOST")
    machine_id = required("ATTENDANCE_MACHINE_ID")
    token = os.environ.get("ATTENDANCE_DEVICE_TOKEN", "").strip()
    secret = os.environ.get("ATTENDANCE_INGEST_SECRET", "").strip()
    if not token and not secret:
        raise RuntimeError("Missing ATTENDANCE_DEVICE_TOKEN or ATTENDANCE_INGEST_SECRET")
    ingest_url = required("ATTENDANCE_INGEST_URL")
    ingest_url = validate_test_ingest_url(ingest_url) if test_events_path else validate_ingest_url(ingest_url)
    timezone_name = os.environ.get("ATTENDANCE_TIMEZONE", "Asia/Kolkata")
    transport = EventTransport(
        ingest_url,
        secret,
        machine_id,
        token=token,
        timeout=int(os.environ.get("ATTENDANCE_HTTP_TIMEOUT", "30")),
        max_retries=int(os.environ.get("ATTENDANCE_HTTP_RETRIES", "3")),
        retry_backoff_seconds=float(os.environ.get("ATTENDANCE_RETRY_BACKOFF_SECONDS", "2")),
    )
    outbox = Outbox(os.environ.get("ATTENDANCE_OUTBOX_PATH", str(Path.home() / ".ides-attendance" / "outbox.db")))
    try:
        if test_events_path:
            events = load_test_events(test_events_path, timezone_name)
        else:
            adapter = EsslX2008Adapter(
                host,
                int(os.environ.get("ATTENDANCE_DEVICE_PORT", "4370")),
                int(os.environ.get("ATTENDANCE_DEVICE_PASSWORD", "0")),
                int(os.environ.get("ATTENDANCE_DEVICE_TIMEOUT", "10")),
                timezone_name,
            )
            events = adapter.read_events()
        for event in events:
            outbox.enqueue(event.event_id, event.as_payload())
        transport.heartbeat()
        sent = 0
        while batch := outbox.pending(500):
            result = transport.send([payload for _, payload in batch])
            outbox.acknowledge(event_id for event_id, _ in batch)
            sent += int(result.get("accepted", len(batch)))
        print(f"device={host} queued={outbox.count()} sent={sent}")
        return 0
    finally:
        outbox.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IDeS attendance device connector")
    parser.add_argument("--once", action="store_true", help="pull and deliver once, then exit")
    parser.add_argument(
        "--test-events",
        metavar="PATH",
        help="replay ZK rows from JSON; sends only to localhost and exits after one run",
    )
    args = parser.parse_args(argv)
    if args.test_events:
        try:
            return run_once(args.test_events)
        except Exception as error:
            print(f"ides-device-connector: {error}", file=sys.stderr)
            return 1
    interval = int(os.environ.get("ATTENDANCE_POLL_SECONDS", "60"))
    while True:
        try:
            run_once()
        except Exception as error:
            print(f"ides-device-connector: {error}", file=sys.stderr)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
