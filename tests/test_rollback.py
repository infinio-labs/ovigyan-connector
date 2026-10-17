"""A newly installed version is on probation; one that keeps failing puts the version it replaced back."""
import json

import pytest

from fake_cloud import PAIR_KEY, FakeCloud
from ovigyan_connector import updater as updater_module
from ovigyan_connector.service import ConnectorService, CycleReport
from ovigyan_connector.state import StateStore
from ovigyan_connector.updater import LINUX_BINARY, PROBATION_FAULTS, PROBATION_SECONDS, ROLLBACK_MARKER, Outcome, Policy, Updater, apply_staged
from test_updater import BASE, NEW, PUBLIC, Host, Runner, release, updater

OLD = "0.3.0"


def on_probation(tmp_path, monkeypatch, applied_ago=0.0, running=NEW, **extra):
    """This process is `running`, installed `applied_ago` seconds ago to replace OLD, and not yet confirmed."""
    import time

    monkeypatch.setattr(updater_module, "__version__", running)
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "update.json").write_text(
        json.dumps({"probation": True, "from_version": OLD, "to_version": NEW, "applied_at": time.time() - applied_ago, **extra})
    )


class TestRecording:
    def test_an_update_that_starts_remembers_what_to_go_back_to(self, tmp_path):
        up, _ = updater(tmp_path)
        assert up.run(Policy(target=NEW)).status == "restart"
        state = up.state()
        assert (state["from_version"], state["to_version"], state["probation"]) == (updater_module.__version__, NEW, True)

    def test_only_the_version_that_was_just_installed_is_on_probation(self, tmp_path, monkeypatch):
        up, _ = updater(tmp_path)
        on_probation(tmp_path, monkeypatch, running="1.0.0")  # a different version is running than the one installed
        assert up.on_probation() is None
        on_probation(tmp_path, monkeypatch)
        assert up.on_probation() is not None

    def test_reaching_the_site_confirms_the_update_and_ends_probation(self, tmp_path, monkeypatch):
        up, _ = updater(tmp_path)
        on_probation(tmp_path, monkeypatch)
        up.confirm()
        assert up.on_probation() is None and up.state()["status"] == "confirmed"

    def test_probation_lasts_fifteen_minutes(self, tmp_path, monkeypatch):
        up, _ = updater(tmp_path)
        on_probation(tmp_path, monkeypatch, applied_ago=PROBATION_SECONDS - 60)
        assert not up.probation_over()
        on_probation(tmp_path, monkeypatch, applied_ago=PROBATION_SECONDS + 60)
        assert up.probation_over()


class TestRollingBack:
    def served_old(self):
        return release(version=OLD)

    def test_the_previous_version_is_fetched_checked_and_staged_with_a_marker(self, tmp_path, monkeypatch):
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host(self.served_old()), platform_name="linux", runner=Runner(version=OLD))
        on_probation(tmp_path, monkeypatch)
        outcome = up.roll_back()
        assert outcome.status == "restart"
        assert (tmp_path / "updates" / ROLLBACK_MARKER).read_text() == NEW  # names the version it replaces
        assert up.state()["rolled_back_from"] == NEW and up.state()["probation"] is False

    def test_on_windows_the_previous_versions_signed_setup_runs_as_the_system_task(self, tmp_path, monkeypatch):
        files = {"ovigyan-connector.exe": b"x", f"ovigyan-connector-setup-{OLD}.exe": b"y"}
        runner = Runner(version=OLD)
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host(release(version=OLD, files=files)), platform_name="windows", runner=runner)
        on_probation(tmp_path, monkeypatch)
        assert up.roll_back().status == "applying"
        create = next(call for call in runner.calls if call[:2] == ["schtasks.exe", "/Create"])
        assert f"ovigyan-connector-setup-{OLD}.exe" in create[-1] and "/VERYSILENT" in create[-1]
        assert ["schtasks.exe", "/Run", "/TN", updater_module.UPDATE_TASK] in runner.calls

    def test_a_previous_version_that_does_not_check_out_is_not_installed(self, tmp_path, monkeypatch):
        served = self.served_old()
        served[f"{BASE}/v{OLD}/{LINUX_BINARY}"] = b"tampered"
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host(served), platform_name="linux", runner=Runner(version=OLD))
        on_probation(tmp_path, monkeypatch)
        assert up.roll_back().status == "failed"
        assert not list((tmp_path / "updates").glob("*"))

    def test_nothing_is_rolled_back_when_nothing_is_on_probation(self, tmp_path):
        up, _ = updater(tmp_path)
        assert up.roll_back().status == "up_to_date"

    def test_the_failed_version_is_not_tried_again_until_the_site_names_another(self, tmp_path, monkeypatch):
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host({**self.served_old(), **release()}), platform_name="linux", runner=Runner(version=OLD))
        on_probation(tmp_path, monkeypatch)
        up.roll_back()
        monkeypatch.setattr(updater_module, "__version__", OLD)  # now the old version is running again
        assert up.run(Policy(target=NEW), now=10**10).status == "held"  # even long after the hourly retry window
        assert up.run(Policy(target="100.0.0")).status != "held"  # a different version is another matter


class TestInstallingAnOlderBuild:
    def stage_old(self, tmp_path, marker=None):
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host(release(version=OLD)), platform_name="linux", runner=Runner(version=OLD))
        up._update(OLD)
        if marker is not None:
            (tmp_path / "updates" / ROLLBACK_MARKER).write_text(marker)
        install = tmp_path / "ovigyan-connector"
        install.write_bytes(b"current")
        return install

    def test_an_older_build_is_installed_only_when_told_which_version_it_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater_module, "__version__", NEW)
        install = self.stage_old(tmp_path, marker=NEW)
        assert apply_staged(tmp_path, install, PUBLIC) == f"installed {OLD}"
        assert install.read_bytes().startswith(b"#!/bin/sh") and not list((tmp_path / "updates").glob("*"))

    def test_without_the_marker_or_with_one_for_another_version_it_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater_module, "__version__", NEW)
        for marker in (None, "1.2.3"):
            install = self.stage_old(tmp_path, marker=marker)
            assert "not newer" in apply_staged(tmp_path, install, PUBLIC) and install.read_bytes() == b"current"

    def test_the_marker_never_lets_a_newer_build_through_as_a_rollback_by_accident(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater_module, "__version__", OLD)  # running the OLD version; the staged build is NEW
        up = Updater(tmp_path, base_url=BASE, public_key=PUBLIC, fetch=Host(release()), platform_name="linux", runner=Runner(version=NEW))
        up._update(NEW)
        (tmp_path / "updates" / ROLLBACK_MARKER).write_text(OLD)
        install = tmp_path / "ovigyan-connector"
        install.write_bytes(b"current")
        assert "not older" in apply_staged(tmp_path, install, PUBLIC)


class Recorder(Updater):
    """An updater whose rollback is only recorded, for the service's decisions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rolled_back = 0

    def roll_back(self):
        self.rolled_back += 1
        return Outcome("restart", "went back")


def ok(connector="ok"):
    return CycleReport("now", connector, "msg", 60, [])


@pytest.fixture
def service(tmp_path, monkeypatch):
    on_probation(tmp_path, monkeypatch, applied_ago=PROBATION_SECONDS + 60)
    up = Recorder(tmp_path, base_url=BASE, public_key=PUBLIC)
    return ConnectorService(StateStore(tmp_path), updater=up), up


class TestServiceDecisions:
    def test_reaching_the_site_confirms_and_nothing_is_rolled_back(self, service):
        svc, up = service
        svc._probation_faults = 99
        assert svc.check_probation(ok("ok")) is False
        assert up.on_probation() is None and up.rolled_back == 0

    def test_a_lost_connection_is_not_a_bad_version_however_long_it_lasts(self, service):
        svc, up = service
        for _ in range(50):
            assert svc.check_probation(ok("offline")) is False
        assert up.rolled_back == 0 and up.on_probation() is not None

    def test_errors_of_its_own_for_the_whole_probation_put_the_old_version_back(self, service):
        svc, up = service
        svc._probation_faults = PROBATION_FAULTS
        import threading

        stop = threading.Event()
        assert svc.check_probation(None, stop) is True
        assert up.rolled_back == 1 and svc.restart_requested and stop.is_set()

    def test_a_few_errors_are_not_enough_and_nor_is_a_short_probation(self, service, tmp_path, monkeypatch):
        svc, up = service
        svc._probation_faults = PROBATION_FAULTS - 1
        assert svc.check_probation(None) is False
        on_probation(tmp_path, monkeypatch, applied_ago=30)  # only just installed
        svc._probation_faults = 99
        assert svc.check_probation(None) is False
        assert up.rolled_back == 0

    def test_with_no_update_pending_nothing_is_watched(self, tmp_path):
        up = Recorder(tmp_path, base_url=BASE, public_key=PUBLIC)
        svc = ConnectorService(StateStore(tmp_path), updater=up)
        svc._probation_faults = 99
        assert svc.check_probation(None) is False and up.rolled_back == 0 and svc._probation_faults == 0
