"""The two extras the connector does on a reachable terminal: its clock, and who is stored on it."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fake_cloud import PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector.devices import ProbeError
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore

PUNCH = lambda uid, user, at: {"uid": uid, "user_id": user, "timestamp": at, "punch": 0, "status": 1}  # noqa: E731
USERS = [{"uid": 1, "user_id": "7", "name": "Aarav Sharma"}, {"uid": 2, "user_id": "8", "name": "Diya Rao"}]


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def kolkata_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None, microsecond=0)


def setup(tmp_path, cloud, zk, **kw):
    kw.setdefault("clock_check_seconds", 0)
    kw.setdefault("users_check_seconds", 0)
    service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, **kw)
    service.pair(cloud.url, PAIR_KEY)
    service.add_device("127.0.0.1", zk.port)
    cloud.approve(zk.serial)
    return service


def user_posts(cloud):
    return [r for r in cloud.requests if r["path"] == "/api/connectors/devices/users"]


def test_the_terminal_clock_is_read_and_reported_to_the_site(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1", clock=kolkata_now() - timedelta(minutes=30)) as zk:
        service = setup(tmp_path, cloud, zk)
        service.run_cycle()  # reads the clock
        offset = service.status()["devices"][0]["clockOffsetSeconds"]
        assert -1810 <= offset <= -1790
        service.run_cycle()  # reports the previous sample, then reads a fresh one
        assert cloud.reported["SN1"]["status"]["clockOffsetSeconds"] == offset


def test_the_clock_is_checked_on_its_own_schedule_not_every_cycle(tmp_path, cloud):
    reads = []
    with FakeZkDevice([], serial="SN1") as zk:
        service = setup(tmp_path, cloud, zk, clock_check_seconds=3600, clock_reader=lambda *a: reads.append(a) or 0)
        for _ in range(3):
            service.run_cycle()
    assert len(reads) == 1


def test_the_people_on_the_terminal_are_sent_once_and_again_only_when_they_change(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1", users=list(USERS)) as zk:
        service = setup(tmp_path, cloud, zk)
        service.run_cycle()
        assert cloud.users["SN1"] == [{"userId": "7", "name": "Aarav Sharma"}, {"userId": "8", "name": "Diya Rao"}]
        service.run_cycle()
        service.run_cycle()
        assert len(user_posts(cloud)) == 1  # an unchanged list is not sent again
        zk.users.append({"uid": 3, "user_id": "9", "name": "Kabir"})
        service.run_cycle()
        assert len(user_posts(cloud)) == 2 and len(cloud.users["SN1"]) == 3


def test_users_are_not_sent_for_a_terminal_that_is_not_approved_yet(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1", users=list(USERS)) as zk:
        service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, users_check_seconds=0)
        service.pair(cloud.url, PAIR_KEY)
        service.add_device("127.0.0.1", zk.port)  # never approved
        service.run_cycle()
    assert user_posts(cloud) == []


def test_an_older_site_without_the_endpoint_never_disturbs_delivery(tmp_path, cloud):
    cloud.users_supported = False
    with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1", users=list(USERS)) as zk:
        service = setup(tmp_path, cloud, zk)
        report = service.run_cycle()
    assert report.devices[0].state == "ok" and len(cloud.received["SN1"]) == 1


def test_a_failing_check_never_stops_punches_being_delivered(tmp_path, cloud):
    def broken(*_args):
        raise ProbeError("unreachable", "the terminal hung up")

    with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1", users=list(USERS)) as zk:
        service = setup(tmp_path, cloud, zk, clock_reader=broken, user_reader=broken)
        report = service.run_cycle()
    assert report.devices[0].state == "ok" and len(cloud.received["SN1"]) == 1


def test_setting_a_terminal_clock_corrects_it_and_the_page_data_follows(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1", clock=kolkata_now() - timedelta(hours=3)) as zk:
        service = setup(tmp_path, cloud, zk)
        service.run_cycle()
        assert service.status()["devices"][0]["clockOffsetSeconds"] < -10000
        device_id = service.status()["devices"][0]["id"]
        offset = service.set_device_clock(device_id)
        assert abs(offset) <= 5
        assert abs(service.status()["devices"][0]["clockOffsetSeconds"]) <= 5
        assert abs((zk.clock - kolkata_now()).total_seconds()) <= 5


def test_setting_the_clock_of_an_unknown_terminal_is_explained(tmp_path, cloud):
    with FakeZkDevice([], serial="SN1") as zk:
        service = setup(tmp_path, cloud, zk)
        with pytest.raises(ProbeError) as caught:
            service.set_device_clock("nope")
    assert caught.value.kind == "not_found"


def test_a_bad_timezone_in_the_clock_check_does_not_stop_delivery(tmp_path, cloud):
    from zoneinfo import ZoneInfoNotFoundError

    def bad_zone(*args):
        raise ZoneInfoNotFoundError("No time zone found with key Mars/Base")

    with FakeZkDevice([PUNCH(1, "7", "2026-10-06T09:00:00")], serial="SN1") as zk:
        service = setup(tmp_path, cloud, zk, clock_reader=bad_zone)
        service.run_cycle()
    assert len(cloud.received["SN1"]) == 1


def test_a_site_answer_this_version_cannot_read_counts_towards_rollback_but_a_lost_connection_does_not(tmp_path, cloud):
    from ovigyan_connector.cloud import CloudError

    with FakeZkDevice([], serial="SN1") as zk:
        service = setup(tmp_path, cloud, zk)

        def fail(kind):
            def config():
                raise CloudError(kind, "x")

            return type("C", (), {"config": staticmethod(config)})()

        for kind, expected in (("unreachable", 0), ("bad_response", 1)):
            service._probation_faults = 0
            service._client = lambda state, kind=kind: fail(kind)
            assert service.run_cycle().connector == "offline"
            assert service._probation_faults == expected
