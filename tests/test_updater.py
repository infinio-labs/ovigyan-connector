import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fake_cloud import PAIR_KEY, FakeCloud
from ovigyan_connector import __version__
from ovigyan_connector.service import ConnectorService, CycleReport, DeviceStatus
from ovigyan_connector.state import StateStore
from ovigyan_connector.updater import LINUX_BINARY, UPDATE_TASK, Policy, Updater, apply_staged, parse_version, verify_signature
from ovigyan_connector.updater import _sha256

BASE = "https://releases.test/dl"
NEW = "99.0.0"


def keypair():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, base64.b64encode(public).decode()


KEY, PUBLIC = keypair()


def release(version=NEW, files=None, key=KEY, manifest_version=None):
    """What the release host would serve: {url: bytes}."""
    files = files if files is not None else {LINUX_BINARY: b"#!/bin/sh\necho new\n"}
    manifest = json.dumps({"version": manifest_version or version, "files": {n: _sha256(d) for n, d in files.items()}}).encode()
    served = {f"{BASE}/v{version}/manifest.json": manifest, f"{BASE}/v{version}/manifest.json.sig": base64.b64encode(key.sign(manifest))}
    served.update({f"{BASE}/v{version}/{name}": data for name, data in files.items()})
    return served


class Host:
    def __init__(self, served):
        self.served, self.requested = served, []

    def __call__(self, url, limit):
        self.requested.append(url)
        if url not in self.served:
            raise OSError(f"404 {url}")
        return self.served[url]


class Runner:
    def __init__(self, ok=True, version=NEW):
        self.calls, self.ok, self.version = [], ok, version

    def __call__(self, args, **_):
        self.calls.append(list(args))
        stdout = f"ovigyan-connector {self.version} ok\n" if args[-1] == "self-test" and self.ok else ""
        return subprocess.CompletedProcess(args, 0 if self.ok else 1, stdout, "")


def updater(tmp_path, served=None, platform="linux", runner=None, public=PUBLIC):
    host = Host(release() if served is None else served)
    return Updater(tmp_path, base_url=BASE, public_key=public, fetch=host, platform_name=platform, runner=runner or Runner()), host


def test_versions_compare_numerically_and_reject_junk():
    assert parse_version("1.10.0") > parse_version("1.9.9")
    assert all(parse_version(v) is None for v in ["", "1.2", "v1.2.3", "1.2.3-beta", None, 5, "1.2.3; rm -rf /"])


class TestSignature:
    def test_a_good_signature_verifies(self):
        assert verify_signature(b"data", base64.b64encode(KEY.sign(b"data")).decode(), PUBLIC)

    def test_tampered_data_another_key_or_garbage_do_not(self):
        sig = base64.b64encode(KEY.sign(b"data")).decode()
        other = keypair()[1]
        assert not verify_signature(b"datA", sig, PUBLIC)
        assert not verify_signature(b"data", sig, other)
        assert not verify_signature(b"data", "not base64!!", PUBLIC)
        assert not verify_signature(b"data", sig, "")


class TestDeciding:
    def test_nothing_to_do_when_current_or_ahead(self, tmp_path):
        up, host = updater(tmp_path)
        for target in (None, __version__, "0.0.1"):
            assert up.run(Policy(target=target)).status == "up_to_date"
        assert host.requested == []

    def test_a_site_that_turns_updates_off_is_obeyed(self, tmp_path):
        up, host = updater(tmp_path)
        assert up.run(Policy(target=NEW, auto=False)).status == "held" and host.requested == []

    def test_a_build_without_a_key_never_updates(self, tmp_path):
        up, host = updater(tmp_path, public="")
        assert up.run(Policy(target=NEW)).status == "disabled" and host.requested == []

    def test_it_waits_for_an_idle_queue_unless_the_site_needs_a_newer_version(self, tmp_path):
        up, host = updater(tmp_path)
        assert up.run(Policy(target=NEW, minimum=__version__), idle=False).status == "waiting" and host.requested == []
        assert up.run(Policy(target=NEW, minimum=NEW), idle=False).status == "restart"

    def test_a_failed_attempt_is_not_repeated_within_the_hour(self, tmp_path):
        up, host = updater(tmp_path, served={})
        assert up.run(Policy(target=NEW), now=1000).status == "failed"
        calls = len(host.requested)
        assert up.run(Policy(target=NEW), now=1000 + 60).status == "waiting" and len(host.requested) == calls
        assert up.run(Policy(target=NEW), now=1000 + 3601).status == "failed" and len(host.requested) > calls
        assert up.run(Policy(target=NEW), now=1000 + 3602, force=True).status == "failed"


class TestLinuxStaging:
    def test_a_verified_build_is_staged_with_its_proof_after_a_self_test(self, tmp_path):
        runner = Runner()
        up, _ = updater(tmp_path, runner=runner)
        assert up.run(Policy(target=NEW)).status == "restart"
        staged = tmp_path / "updates"
        assert (staged / LINUX_BINARY).read_bytes().startswith(b"#!/bin/sh") and (staged / "manifest.json.sig").exists()
        assert runner.calls == [[str(staged / LINUX_BINARY), "self-test"]]
        assert up.state()["status"] == "restart"

    @pytest.mark.parametrize(
        "served,why",
        [
            (release(key=keypair()[0]), "signature"),
            (release(manifest_version="98.0.0"), "different version"),
            ({**release(), f"{BASE}/v{NEW}/{LINUX_BINARY}": b"tampered"}, "checksum"),
            ({k: v for k, v in release().items() if not k.endswith(LINUX_BINARY)}, "missing file"),
        ],
    )
    def test_anything_that_does_not_check_out_is_refused_and_leaves_nothing_behind(self, tmp_path, served, why):
        up, _ = updater(tmp_path, served=served)
        outcome = up.run(Policy(target=NEW))
        assert outcome.status == "failed", why
        assert not list((tmp_path / "updates").glob("*")) if (tmp_path / "updates").exists() else True

    def test_a_build_that_fails_its_self_test_is_not_staged(self, tmp_path):
        up, _ = updater(tmp_path, runner=Runner(ok=False))
        outcome = up.run(Policy(target=NEW))
        assert outcome.status == "failed" and "did not start" in outcome.message
        assert not list((tmp_path / "updates").glob("*"))


class TestWindows:
    def test_the_exe_is_self_tested_then_the_setup_runs_as_a_separate_system_task(self, tmp_path):
        setup = f"ovigyan-connector-setup-{NEW}.exe"
        runner = Runner()
        up, host = updater(tmp_path, served=release(files={"ovigyan-connector.exe": b"MZexe", setup: b"MZsetup"}), platform="windows", runner=runner)
        assert up.run(Policy(target=NEW)).status == "applying"
        self_test, create, run = runner.calls
        assert self_test[-1] == "self-test" and self_test[0].endswith("ovigyan-connector.exe")
        assert create[:4] == ["schtasks.exe", "/Create", "/TN", UPDATE_TASK] and "/RU" in create and "SYSTEM" in create
        assert setup in create[-1] and "/VERYSILENT" in create[-1] and run == ["schtasks.exe", "/Run", "/TN", UPDATE_TASK]
        assert (tmp_path / "updates" / setup).read_bytes() == b"MZsetup"

    def test_a_failed_self_test_never_runs_the_installer(self, tmp_path):
        runner = Runner(ok=False)
        up, _ = updater(tmp_path, served=release(files={"ovigyan-connector.exe": b"x", f"ovigyan-connector-setup-{NEW}.exe": b"y"}), platform="windows", runner=runner)
        assert up.run(Policy(target=NEW)).status == "failed"
        assert all(call[0] != "schtasks.exe" for call in runner.calls)


class TestApplyStaged:
    def stage(self, tmp_path, served=None):
        up, _ = updater(tmp_path, served=served)
        assert up.run(Policy(target=NEW)).status == "restart"
        install = tmp_path / "bin" / "ovigyan-connector"
        install.parent.mkdir()
        install.write_bytes(b"old")
        return install

    def test_swaps_in_the_staged_build_keeps_the_old_one_and_clears_the_staging_area(self, tmp_path):
        install = self.stage(tmp_path)
        assert apply_staged(tmp_path, install, PUBLIC) == f"installed {NEW}"
        assert install.read_bytes().startswith(b"#!/bin/sh") and install.stat().st_mode & 0o111
        assert (install.parent / "ovigyan-connector.previous").read_bytes() == b"old"
        assert not list((tmp_path / "updates").glob("*"))

    def test_nothing_staged_is_not_an_error(self, tmp_path):
        assert apply_staged(tmp_path, tmp_path / "x", PUBLIC) == "nothing staged"

    def test_a_staged_file_that_was_swapped_after_the_check_is_refused(self, tmp_path):
        install = self.stage(tmp_path)
        (tmp_path / "updates" / LINUX_BINARY).write_bytes(b"evil")
        assert "checksum" in apply_staged(tmp_path, install, PUBLIC) and install.read_bytes() == b"old"
        assert not list((tmp_path / "updates").glob("*"))

    def test_a_staged_manifest_signed_by_someone_else_is_refused(self, tmp_path):
        install = self.stage(tmp_path)
        assert "signature" in apply_staged(tmp_path, install, keypair()[1]) and install.read_bytes() == b"old"

    def test_an_older_or_equal_version_is_refused(self, tmp_path):
        old = "0.0.1"
        up, _ = updater(tmp_path, served=release(version=old))
        up.fetch, up.runner = Host(release(version=old)), Runner(version=old)
        up._update(old)  # staging has no version floor; applying does
        install = tmp_path / "ovigyan-connector"
        install.write_bytes(b"old")
        assert "not newer" in apply_staged(tmp_path, install, PUBLIC) and install.read_bytes() == b"old"

    def test_symlinks_in_the_staging_area_are_refused(self, tmp_path):
        install = self.stage(tmp_path)
        binary = tmp_path / "updates" / LINUX_BINARY
        target = tmp_path / "elsewhere"
        target.write_bytes(binary.read_bytes())
        binary.unlink()
        binary.symlink_to(target)
        assert "regular files" in apply_staged(tmp_path, install, PUBLIC) and install.read_bytes() == b"old"


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def report(queued=0, connector="ok"):
    return CycleReport("now", connector, "msg", 60, [DeviceStatus(id="d", host="h", port=1, serial="S", model=None, state="ok", message="", queued=queued)])


class TestService:
    def paired(self, tmp_path, cloud, **kw):
        service = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, **kw)
        service.pair(cloud.url, PAIR_KEY)
        return service

    def test_the_sites_version_policy_is_read_from_its_config(self, tmp_path, cloud):
        service = self.paired(tmp_path, cloud)
        cloud.update = {"target": "1.2.3", "minimum": "1.0.0", "auto": False}
        service.run_cycle()
        assert service.policy == Policy(target="1.2.3", minimum="1.0.0", auto=False)

    def test_an_older_site_without_a_policy_changes_nothing(self, tmp_path, cloud):
        service = self.paired(tmp_path, cloud)
        service.run_cycle()
        assert service.policy is None and service.check_update(report()) is False

    def test_a_busy_queue_waits_and_an_idle_one_restarts(self, tmp_path, cloud):
        up, _ = updater(tmp_path / "u")
        service = self.paired(tmp_path, cloud, updater=up)
        service.policy = Policy(target=NEW)
        assert service.check_update(report(queued=3)) is False and not service.restart_requested
        assert service.check_update(report(queued=0)) is True and service.restart_requested

    def test_a_cycle_that_did_not_reach_the_site_never_updates(self, tmp_path, cloud):
        up, host = updater(tmp_path / "u")
        service = self.paired(tmp_path, cloud, updater=up)
        service.policy = Policy(target=NEW)
        assert service.check_update(report(connector="offline")) is False and host.requested == []

    def test_run_forever_stops_itself_after_staging_so_the_service_manager_restarts_it(self, tmp_path, cloud):
        import threading

        up, _ = updater(tmp_path / "u")
        service = self.paired(tmp_path, cloud, updater=up)
        cloud.update = {"target": NEW}
        done = threading.Thread(target=service.run_forever, args=(threading.Event(),), kwargs={"fixed_wait": 0.01}, daemon=True)
        done.start()
        done.join(15)
        assert not done.is_alive() and service.restart_requested


def test_self_test_runs_in_a_real_process():
    done = subprocess.run([sys.executable, "-m", "ovigyan_connector.cli", "self-test"], capture_output=True, text=True, timeout=60)
    assert (done.returncode, done.stdout.strip()) == (0, f"ovigyan-connector {__version__} ok")
