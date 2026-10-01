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
