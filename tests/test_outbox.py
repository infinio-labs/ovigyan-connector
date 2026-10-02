from ides_device_connector.outbox import Outbox


def test_outbox_is_idempotent_and_acknowledgeable(tmp_path):
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.enqueue("event-1", {"eventId": "event-1"})
    outbox.enqueue("event-1", {"eventId": "event-1", "different": True})
    assert outbox.count() == 1
    assert outbox.pending() == [("event-1", {"eventId": "event-1"})]
    outbox.acknowledge(["event-1"])
    assert outbox.count() == 0
    outbox.close()


def test_acknowledged_events_are_not_requeued_and_excluded_ids_are_skipped(tmp_path):
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.enqueue("a", {"eventId": "a"})
    outbox.enqueue("b", {"eventId": "b"})
    assert [i for i, _ in outbox.pending(exclude={"a"})] == ["b"]
    outbox.acknowledge(["a"])
    outbox.enqueue("a", {"eventId": "a"})  # device log re-read after delivery
    assert outbox.count() == 1
    outbox.close()


def test_quarantined_events_leave_the_queue_and_are_not_requeued(tmp_path):
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.enqueue("bad", {"eventId": "bad"})
    outbox.enqueue("good", {"eventId": "good"})
    outbox.quarantine("bad", "Device event timestamp is invalid.")
    assert [i for i, _ in outbox.pending()] == ["good"]
    outbox.enqueue("bad", {"eventId": "bad"})  # the terminal log is re-read on every poll
    assert outbox.count() == 1
    assert outbox.rejected_count() == 1
    outbox.close()
