"""Delivering queued punches to the cloud, surviving a bad event without losing the good ones."""
from __future__ import annotations

import json
import sys
import urllib.error

from .outbox import Outbox


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
            print(f"ovigyan-connector: quarantined event {batch[0][0]}: {reason}", file=sys.stderr)
            return 0
        middle = len(batch) // 2
        return deliver(transport, outbox, batch[:middle], deferred) + deliver(transport, outbox, batch[middle:], deferred)
    held = set(result.get("deferredEventIds", []))
    deferred |= held
    outbox.acknowledge(event_id for event_id, _ in batch if event_id not in held)
    return int(result.get("accepted", len(batch)))
