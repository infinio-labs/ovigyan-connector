"""When the connector takes the school's copy of its data, and that nothing about it can disturb attendance."""
from datetime import datetime, timedelta

import pytest

from fake_cloud import PAIR_KEY, FakeCloud, make_backup_zip
from fake_zk_device import FakeZkDevice
from ovigyan_connector.backup import BackupError
from ovigyan_connector.cloud import CloudError
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore

DAY = datetime(2026, 10, 16)


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def make(tmp_path, cloud, clock=None, **kw):
    service = ConnectorService(
        StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, backup_clock=clock or (lambda: DAY.replace(hour=1, minute=30)), **kw
    )
    service.pair(cloud.url, PAIR_KEY)
    service.set_backup_folder(str(tmp_path / "copies"))
    return service


def finish(service):
    if service._backup_thread:
        service._backup_thread.join(10)


def test_a_switched_on_connector_takes_its_first_copy_straight_away(tmp_path, cloud):
    cloud.backup = True
    service = make(tmp_path, cloud)
    assert service.run_cycle().connector == "ok"
    finish(service)
    copies = sorted((tmp_path / "copies").glob("ovigyan-backup-*.zip"))
    assert [c.name for c in copies] == ["ovigyan-backup-2026-10-16-0130.zip"]
    status = service.backup_status()
    assert status["enabled"] is True and status["error"] is None and not status["running"]
    assert status["last"]["file"] == copies[0].name and status["last"]["bytes"] == len(make_backup_zip())
    assert service.status()["backup"]["last"]["file"] == copies[0].name  # the dashboard sees the same


@pytest.mark.parametrize("flag", [None, False])
def test_nothing_is_taken_unless_the_school_switched_it_on(tmp_path, cloud, flag):
    cloud.backup = flag  # None: an older site that sends no backup block at all
    service = make(tmp_path, cloud)
    service.run_cycle()
    finish(service)
    assert not list((tmp_path / "copies").glob("*.zip")) and not any(r["path"] == "/api/connectors/backup" for r in cloud.requests)
    assert service.backup_status()["enabled"] is False  # an older site's silence means off, like an explicit no


class TestWhenItIsDue:
    def note(self, service, **fields):
        service._save_backup_note(**fields)

    def test_one_a_night_in_the_small_hours_and_a_catch_up_after_two_missed_days(self, tmp_path, cloud):
        service = make(tmp_path, cloud)
        last = DAY.replace(hour=1, minute=30)
        self.note(service, lastSuccessAt=last.isoformat())
        assert not service.backup_due(last.replace(hour=14))  # same day
        assert not service.backup_due(last + timedelta(hours=10))  # not yet a night later
        assert service.backup_due(last + timedelta(days=1))  # next night, 01:30
        assert not service.backup_due(last + timedelta(days=1, hours=12))  # 13:30 next day: wait for the night
        assert service.backup_due(last + timedelta(days=2, hours=12))  # two days without one: now, whatever the hour

    def test_the_first_copy_has_no_waiting(self, tmp_path, cloud):
        assert make(tmp_path, cloud).backup_due(DAY.replace(hour=15))

    def test_after_a_failure_it_rests_an_hour_before_trying_again(self, tmp_path, cloud):
        service = make(tmp_path, cloud)
        failed = DAY.replace(hour=2)
        self.note(service, lastAttemptAt=failed.isoformat(), lastError="no space")
        assert not service.backup_due(failed + timedelta(minutes=30))
        assert service.backup_due(failed + timedelta(minutes=61))


def test_only_one_copy_runs_at_a_time(tmp_path, cloud):
    import threading

    cloud.backup = True
    gate, calls = threading.Event(), []

    def slow(client, folder, **kw):
        calls.append(1)
        gate.wait(5)
        raise BackupError("stopped by the test")

    service = make(tmp_path, cloud, backup_runner=slow)
    service.run_cycle()
    service.run_cycle()  # the first is still running
    assert len(calls) == 1 and service.backup_status()["running"] is True
    with pytest.raises(BackupError, match="already being taken"):
        service.backup_now()
    gate.set()
    finish(service)


@pytest.mark.parametrize(
    "failure, shown",
    [
        (CloudError("backup_off", "A nightly copy to this PC is not switched on."), "not switched on"),
        (BackupError("There is not enough free space in D:\\"), "not enough free space"),
        (OSError("disk on fire"), "disk on fire"),
        (RuntimeError("boom"), "could not be taken"),
    ],
)
def test_a_copy_that_fails_is_reported_and_never_disturbs_the_connector(tmp_path, cloud, failure, shown):
    cloud.backup = True
    cloud.approve("SN1")

    def broken(*_a, **_k):
        raise failure

    with FakeZkDevice([{"uid": 1, "user_id": "7", "timestamp": "2026-10-06T09:00:00", "punch": 0, "status": 1}], serial="SN1") as zk:
        service = make(tmp_path, cloud, backup_runner=broken)
        service.add_device("127.0.0.1", zk.port)
        report = service.run_cycle()
        finish(service)
    assert report.connector == "ok" and report.devices[0].state == "ok" and len(cloud.received["SN1"]) == 1
    assert shown in service.backup_status()["error"] and service.backup_status()["last"] is None


def test_backup_now_needs_the_school_to_have_switched_it_on(tmp_path, cloud):
    cloud.backup = False
    service = make(tmp_path, cloud)
    service.run_cycle()
    with pytest.raises(BackupError, match="not switched on"):
        service.backup_now()


def test_backup_now_takes_a_copy_even_when_one_is_not_due(tmp_path, cloud):
    cloud.backup = True
    clock = {"now": DAY.replace(hour=1, minute=30)}
    service = make(tmp_path, cloud, clock=lambda: clock["now"])
    service.run_cycle()
    finish(service)
    clock["now"] = DAY.replace(hour=14)
    service.run_cycle()
    finish(service)
    assert len(list((tmp_path / "copies").glob("*.zip"))) == 1  # not due: nothing new
    service.backup_now()
    finish(service)
    assert len(list((tmp_path / "copies").glob("*.zip"))) == 2


class TestFolder:
    def test_the_chosen_folder_is_kept_and_used(self, tmp_path, cloud):
        service = make(tmp_path, cloud)
        chosen = tmp_path / "elsewhere" / "backups"
        assert service.set_backup_folder(str(chosen)) == str(chosen)
        assert StateStore(tmp_path).load().backup_dir == str(chosen)
        assert service.backup_status()["folder"] == str(chosen)

    def test_the_default_is_a_folder_inside_the_connectors_own(self, tmp_path, cloud):
        service = ConnectorService(StateStore(tmp_path))
        assert service.backup_folder() == tmp_path / "backups"

    def test_a_folder_that_cannot_be_used_is_refused_and_the_old_one_kept(self, tmp_path, cloud):
        service = make(tmp_path, cloud)
        before = service.backup_status()["folder"]
        with pytest.raises(BackupError, match="full path"):
            service.set_backup_folder("relative/path")
        assert service.backup_status()["folder"] == before
