from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .adapters import EsslX2008Adapter
from .outbox import Outbox
from .transport import EventTransport


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run_once() -> int:
    host = required("ATTENDANCE_DEVICE_HOST")
    machine_id = required("ATTENDANCE_MACHINE_ID")
    transport = EventTransport(required("ATTENDANCE_INGEST_URL"), required("ATTENDANCE_INGEST_SECRET"), machine_id)
    outbox = Outbox(os.environ.get("ATTENDANCE_OUTBOX_PATH", str(Path.home() / ".ides-attendance" / "outbox.db")))
    adapter = EsslX2008Adapter(
        host,
        int(os.environ.get("ATTENDANCE_DEVICE_PORT", "4370")),
        int(os.environ.get("ATTENDANCE_DEVICE_PASSWORD", "0")),
        int(os.environ.get("ATTENDANCE_DEVICE_TIMEOUT", "10")),
        os.environ.get("ATTENDANCE_TIMEZONE", "Asia/Kolkata"),
    )
    try:
        for event in adapter.read_events():
            outbox.enqueue(event.event_id, event.as_payload())
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
    args = parser.parse_args(argv)
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
