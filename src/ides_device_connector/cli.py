from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
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


def _event_rejection(error: urllib.error.HTTPError) -> str | None:
    """The cloud's reason when it refused the batch because of an event's own content, else None.

    Only that case may be quarantined. A 400 about the request itself (identity, JSON, batch size),
    or any 401/409/5xx, is a configuration or outage problem and must keep stopping the run: treating
    it as a bad event would throw away the whole queue.
    """
    if error.code != 400:
        return None
    try:
        message = json.loads(error.read()).get("error", "")
    except (ValueError, AttributeError, OSError):
        return None
    return message if isinstance(message, str) and message.startswith("Device event") else None


def deliver(transport, outbox: Outbox, batch: list, deferred: set[str]) -> int:
    """Send one batch; return how many events the cloud accepted.

    A batch the cloud refuses for an event's content is halved until the offending event stands alone,
    then quarantined, so one bad punch cannot keep every later punch from reaching the school.
    """
    try:
        result = transport.send([payload for _, payload in batch])
    except urllib.error.HTTPError as error:
        reason = _event_rejection(error)
        if reason is None:
            raise
        if len(batch) == 1:
            outbox.quarantine(batch[0][0], reason)
            print(f"ides-device-connector: quarantined event {batch[0][0]}: {reason}", file=sys.stderr)
            return 0
        middle = len(batch) // 2
        return deliver(transport, outbox, batch[:middle], deferred) + deliver(transport, outbox, batch[middle:], deferred)
    held = set(result.get("deferredEventIds", []))
    deferred |= held
    outbox.acknowledge(event_id for event_id, _ in batch if event_id not in held)
    return int(result.get("accepted", len(batch)))


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
        deferred: set[str] = set()  # events the cloud cannot take yet (unmapped terminal user); retried next poll
        while batch := outbox.pending(500, deferred):
            sent += deliver(transport, outbox, batch, deferred)
        rejected = outbox.rejected_count()
        print(f"device={host} queued={outbox.count()} sent={sent}" + (f" rejected={rejected}" if rejected else ""))
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
