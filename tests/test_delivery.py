"""A batch the cloud rejects must not block everything queued behind it."""
import io
import json
import urllib.error

import pytest

from ides_device_connector.cli import deliver
from ides_device_connector.outbox import Outbox


class FakeTransport:
    """Rejects any batch containing a poison event with the cloud's per-event 400."""

    def __init__(self, poison=(), status=400, message="Device event timestamp is invalid."):
        self.poison, self.status, self.message = set(poison), status, message
        self.calls = []

    def send(self, events):
        ids = [event["eventId"] for event in events]
        self.calls.append(ids)
        if self.poison & set(ids):
            body = json.dumps({"error": self.message}).encode()
            raise urllib.error.HTTPError("https://x/events", self.status, "Bad", {}, io.BytesIO(body))
        return {"accepted": len(ids)}


def queued(tmp_path, ids):
    outbox = Outbox(tmp_path / "outbox.db")
    for event_id in ids:
        outbox.enqueue(event_id, {"eventId": event_id})
    return outbox


def test_one_bad_event_is_quarantined_and_the_rest_are_delivered(tmp_path):
    outbox = queued(tmp_path, [f"e{i}" for i in range(7)])
    transport = FakeTransport(poison={"e4"})
    sent = deliver(transport, outbox, outbox.pending(500), set())
    assert sent == 6
    assert outbox.count() == 0
    assert outbox.rejected_count() == 1
    outbox.close()


def test_an_ordinary_batch_is_one_request(tmp_path):
    outbox = queued(tmp_path, ["a", "b", "c"])
    transport = FakeTransport()
    assert deliver(transport, outbox, outbox.pending(500), set()) == 3
    assert len(transport.calls) == 1
    outbox.close()


@pytest.mark.parametrize(
    "status,message",
    [(400, "Missing device identity."), (400, "Invalid JSON."), (401, "Unauthorized"), (409, "Device branch is not active.")],
)
def test_errors_that_are_not_about_one_event_still_stop_the_run(tmp_path, status, message):
    """A bad token or config must never quarantine the whole queue."""
    outbox = queued(tmp_path, ["a", "b"])
    transport = FakeTransport(poison={"a"}, status=status, message=message)
    with pytest.raises(urllib.error.HTTPError):
        deliver(transport, outbox, outbox.pending(500), set())
    assert outbox.count() == 2
    assert outbox.rejected_count() == 0
    outbox.close()


def test_deferred_events_stay_queued(tmp_path):
    class Deferring:
        def send(self, events):
            return {"accepted": 0, "deferredEventIds": ["b"]}

    outbox = queued(tmp_path, ["a", "b"])
    deferred: set[str] = set()
    deliver(Deferring(), outbox, outbox.pending(500), deferred)
    assert deferred == {"b"}
    assert [i for i, _ in outbox.pending()] == ["b"]
    outbox.close()
