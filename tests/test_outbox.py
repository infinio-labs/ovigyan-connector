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
