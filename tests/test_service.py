import json
import threading

import pytest

from fake_cloud import CREDENTIAL, PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector.cloud import CloudError
from ovigyan_connector.devices import ProbeError
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore

PUNCH = lambda uid, user, at, punch=0: {"uid": uid, "user_id": user, "timestamp": at, "punch": punch, "status": 1}  # noqa: E731


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def make(tmp_path, **kw):
    return ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, **kw)


def paired(tmp_path, cloud, **kw):
    service = make(tmp_path, **kw)
    service.pair(cloud.url, PAIR_KEY)
    return service


class TestPairing:
    def test_pairing_saves_the_credential_and_marks_the_connector_paired(self, tmp_path, cloud):
        state = make(tmp_path).pair(f"{cloud.url}/some/page", PAIR_KEY)
        assert (state.paired, state.server, state.credential, state.connector_name) == (True, cloud.url, CREDENTIAL, "Front office PC")
        assert StateStore(tmp_path).load().paired

    def test_a_wrong_key_changes_nothing(self, tmp_path, cloud):
        with pytest.raises(CloudError) as caught:
            make(tmp_path).pair(cloud.url, "OVG-XXXXX-XXXXX-XXXXX-XXXXX")
        assert caught.value.kind == "invalid_key"
        assert not StateStore(tmp_path).load().paired

    def test_a_bad_address_is_refused_before_any_request(self, tmp_path, cloud):
        with pytest.raises(CloudError) as caught:
            make(tmp_path).pair("", PAIR_KEY)
        assert caught.value.kind == "bad_address" and cloud.requests == []

    def test_unpairing_forgets_the_credential_but_keeps_terminals(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            state = service.unpair()
        assert (state.paired, state.credential, state.status) == (False, None, "unpaired") and len(state.devices) == 1

    def test_pairing_again_after_revocation_works(self, tmp_path, cloud):
        service = paired(tmp_path, cloud)
        cloud.revoked = True
        assert service.run_cycle().connector == "revoked"
        cloud.revoked, cloud.key_used = False, False
        service.pair(cloud.url, PAIR_KEY)
        assert service.run_cycle().connector == "ok"


class TestAddingTerminals:
    def test_a_reachable_terminal_is_saved_and_reported_to_the_cloud(self, tmp_path, cloud):
        with FakeZkDevice([], serial="NFZ1", model="X2008") as zk:
            service = paired(tmp_path, cloud)
            device, existing = service.add_device("127.0.0.1", zk.port, 0)
        assert (device.serial, device.model, existing) == ("NFZ1", "X2008", False)
        assert cloud.reported["NFZ1"]["host"] == "127.0.0.1" and cloud.reported["NFZ1"]["port"] == zk.port
        assert cloud.state_of("NFZ1") == "pending"

    def test_adding_works_before_pairing_and_is_reported_at_the_first_cycle(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as zk:
            service = make(tmp_path)
            service.add_device("127.0.0.1", zk.port)
            assert cloud.requests == []
            service.pair(cloud.url, PAIR_KEY)
            service.run_cycle()
        assert "SN1" in cloud.reported

    def test_adding_the_same_terminal_again_updates_its_address_instead_of_duplicating(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as a, FakeZkDevice([], serial="SN1") as b:
            service = paired(tmp_path, cloud)
            first, _ = service.add_device("127.0.0.1", a.port)
            second, existing = service.add_device("127.0.0.1", b.port)
        assert existing and second.id == first.id and second.port == b.port
        assert len(StateStore(tmp_path).load().devices) == 1

    def test_an_unreachable_terminal_is_not_saved(self, tmp_path, cloud):
        service = paired(tmp_path, cloud)
        with pytest.raises(ProbeError):
            service.add_device("127.0.0.1", 1)
        assert StateStore(tmp_path).load().devices == []

    def test_a_cloud_outage_does_not_stop_a_terminal_being_added(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            cloud.force_status = 503
            device, _ = service.add_device("127.0.0.1", zk.port)
        assert device.serial == "SN1" and len(StateStore(tmp_path).load().devices) == 1

    def test_removing_a_terminal(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            device, _ = service.add_device("127.0.0.1", zk.port)
        assert service.remove_device(device.id) is True
        assert service.remove_device(device.id) is False
        assert StateStore(tmp_path).load().devices == []


class TestCycle:
    def test_not_paired(self, tmp_path):
        report = make(tmp_path).run_cycle()
        assert report.connector == "unpaired" and "Not paired" in report.message

    def test_a_terminal_waits_for_approval_and_nothing_is_sent_meanwhile(self, tmp_path, cloud):
        with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            report = service.run_cycle()
        assert report.connector == "ok"
        assert [(d.state, "approve" in d.message) for d in report.devices] == [("pending", True)]
        assert cloud.received == {} and cloud.heartbeats == []

    def test_once_approved_punches_are_delivered_with_the_connector_credential(self, tmp_path, cloud):
        rows = [PUNCH(1, "7", "2026-10-06T09:00:00", 0), PUNCH(2, "7", "2026-10-06T17:00:00", 1)]
        with FakeZkDevice(rows, serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            cloud.approve("SN1")
            report = service.run_cycle()
        assert [(d.state, d.sent, d.queued) for d in report.devices] == [("ok", 2, 0)]
        assert len(cloud.received["SN1"]) == 2 and cloud.heartbeats == ["SN1"]
        event_request = next(r for r in cloud.requests if r["path"] == "/api/attendance/device-events")
        headers = {k.lower(): v for k, v in event_request["headers"].items()}
        assert headers["authorization"] == f"Bearer {CREDENTIAL}" and headers["x-device-machine-id"] == "SN1"
        assert "x-device-token" not in headers and "x-attendance-signature" not in headers

    def test_punches_are_never_sent_twice(self, tmp_path, cloud):
        with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            cloud.approve("SN1")
            service.run_cycle()
            zk.records.append(PUNCH(2, "8", "2026-10-06T09:05:00"))
            second = service.run_cycle()
        assert second.devices[0].sent == 1 and len(cloud.received["SN1"]) == 2

    def test_history_before_the_cutoff_is_not_sent(self, tmp_path, cloud):
        rows = [PUNCH(1, "7", "2026-10-05T09:00:00"), PUNCH(2, "7", "2026-10-05T23:59:00"), PUNCH(3, "7", "2026-10-06T00:00:00"), PUNCH(4, "7", "2026-10-06T09:00:00")]
        with FakeZkDevice(rows, serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            cloud.approve("SN1", ignore_before="2026-10-05T18:30:00.000Z")  # midnight 6 Oct in Asia/Kolkata
            service.run_cycle()
        # exactly the punches at or after the cutoff (delivery order is not significant)
        assert sorted(e["raw"]["timestamp"] for e in cloud.received["SN1"]) == ["2026-10-05T18:30:00.000Z", "2026-10-06T03:30:00.000Z"]

    def test_rejected_and_switched_off_terminals_are_not_polled(self, tmp_path, cloud):
        with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1") as a, FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN2") as b:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", a.port)
            service.add_device("127.0.0.1", b.port)
            cloud.rejected.add("SN1")
            cloud.approve("SN2", enabled=False)
            report = service.run_cycle()
        assert {d.serial: d.state for d in report.devices} == {"SN1": "rejected", "SN2": "disabled"}
        assert cloud.received == {}

    def test_a_terminal_that_is_off_is_reported_and_the_others_still_work(self, tmp_path, cloud):
        with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="GOOD") as good:
            service = paired(tmp_path, cloud)
            with FakeZkDevice([], serial="DOWN") as down:
                service.add_device("127.0.0.1", down.port)
            service.add_device("127.0.0.1", good.port)
            cloud.approve("GOOD")
            cloud.approve("DOWN")
            report = service.run_cycle()
        states = {d.serial: d for d in report.devices}
        assert states["DOWN"].state == "unreachable" and "Can't reach the terminal" in states["DOWN"].message
        assert states["GOOD"].state == "ok" and states["GOOD"].sent == 1

    def test_punches_queued_while_the_cloud_was_down_are_delivered_later(self, tmp_path, cloud):
        with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            cloud.approve("SN1")
            service.run_cycle()
            zk.records.append(PUNCH(2, "7", "2026-10-06T10:00:00"))
            # the cloud goes down: the new punch stays queued
            original = cloud.force_status
            cloud.force_status = 500
            report = service.run_cycle()
            assert report.connector == "offline"
            cloud.force_status = original
            report = service.run_cycle()
        assert len(cloud.received["SN1"]) == 2 and report.devices[0].queued == 0

    def test_two_terminals_with_the_same_user_and_time_both_deliver(self, tmp_path, cloud):
        row = PUNCH(1, "7", "2026-10-06T09:00:00")
        with FakeZkDevice([row], serial="A") as a, FakeZkDevice([row], serial="B") as b:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", a.port)
            service.add_device("127.0.0.1", b.port)
            cloud.approve("A")
            cloud.approve("B")
            service.run_cycle()
        assert (len(cloud.received["A"]), len(cloud.received["B"])) == (1, 1)

    def test_a_revoked_connector_is_told_so_and_stops(self, tmp_path, cloud):
        service = paired(tmp_path, cloud)
        cloud.revoked = True
        report = service.run_cycle()
        assert report.connector == "revoked" and "Pair it again" in report.message
        assert StateStore(tmp_path).load().status == "revoked"
        assert service.run_cycle().connector == "revoked"  # no more calls with a dead credential
        assert len([r for r in cloud.requests if r["path"] != "/api/connectors/pair"]) == 1

    def test_an_unreachable_cloud_means_offline_not_an_error(self, tmp_path):
        service = make(tmp_path)
        store = StateStore(tmp_path)
        state = store.load()
        state.server, state.credential, state.status = "http://127.0.0.1:1", CREDENTIAL, "paired"
        store.save(state)
        report = service.run_cycle()
        assert report.connector == "offline" and "Can't reach" in report.message
        assert store.load().paired  # still paired; it will recover

    def test_the_poll_interval_comes_from_the_cloud(self, tmp_path, cloud):
        cloud.poll_seconds = 120
        assert paired(tmp_path, cloud).run_cycle().poll_seconds == 120

    def test_each_cycle_leaves_a_status_note_without_secrets(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            service.run_cycle()
        text = StateStore(tmp_path).status_path.read_text()
        note = json.loads(text)
        assert note["connector"] == "ok" and note["devices"][0]["serial"] == "SN1"
        assert CREDENTIAL not in text and "password" not in text.lower()


class TestStatusAndLoop:
    def test_status_summarises_pairing_terminals_and_the_last_cycle(self, tmp_path, cloud):
        with FakeZkDevice([], serial="SN1", model="X2008") as zk:
            service = paired(tmp_path, cloud)
            service.add_device("127.0.0.1", zk.port)
            service.run_cycle()
        status = service.status()
        assert status["paired"] and status["connectorName"] == "Front office PC"
        assert status["devices"][0]["serial"] == "SN1" and status["lastCycle"]["connector"] == "ok"
        assert CREDENTIAL not in json.dumps(status)

    def test_the_loop_runs_cycles_until_stopped(self, tmp_path, cloud):
        service = paired(tmp_path, cloud)
        stop = threading.Event()
        cycles = []
        original = service.run_cycle

        def counting():
            report = original()
            cycles.append(report)
            if len(cycles) == 2:
                stop.set()
            return report

        service.run_cycle = counting  # type: ignore[method-assign]
        cloud.poll_seconds = 10
        thread = threading.Thread(target=service.run_forever, args=(stop, 0.01), daemon=True)
        thread.start()
        thread.join(timeout=15)
        assert not thread.is_alive() and len(cycles) == 2

    def test_a_crash_in_one_cycle_does_not_end_the_loop(self, tmp_path, cloud):
        service = paired(tmp_path, cloud)
        stop = threading.Event()
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("unexpected")
            stop.set()
            return service.__class__.run_cycle(service)

        service.run_cycle = flaky  # type: ignore[method-assign]
        thread = threading.Thread(target=service.run_forever, args=(stop, 0.01), daemon=True)
        thread.start()
        thread.join(timeout=20)
        assert not thread.is_alive() and len(calls) == 2


class _RecordingStop:
    """Stands in for threading.Event: records each pause and stops after `limit` of them."""

    def __init__(self, limit):
        self.limit, self.waits = limit, []

    def is_set(self):
        return len(self.waits) >= self.limit

    def wait(self, seconds):
        self.waits.append(seconds)


class TestPacing:
    def test_a_healthy_connector_waits_the_clouds_poll_interval(self, tmp_path, cloud):
        cloud.poll_seconds = 90
        stop = _RecordingStop(2)
        paired(tmp_path, cloud).run_forever(stop)
        assert stop.waits == [90.0, 90.0]

    def test_an_unpaired_connector_idles_briefly_so_pairing_is_noticed_quickly(self, tmp_path):
        stop = _RecordingStop(2)
        make(tmp_path).run_forever(stop)
        assert stop.waits == [15.0, 15.0]

    def test_an_offline_connector_backs_off_and_recovers(self, tmp_path):
        store = StateStore(tmp_path)
        state = store.load()
        state.server, state.credential, state.status = "http://127.0.0.1:1", CREDENTIAL, "paired"
        store.save(state)
        stop = _RecordingStop(4)
        make(tmp_path).run_forever(stop)
        assert stop.waits == [120.0, 240.0, 300.0, 300.0]  # doubles, capped at five minutes
