import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Linux install scripts need bash")


@pytest.fixture
def kit(tmp_path):
    """The install kit as the release ships it: scripts, unit file and the executable in one folder."""
    folder = tmp_path / "kit"
    folder.mkdir()
    for name in ("install.sh", "uninstall.sh"):
        shutil.copy(PACKAGING / "linux" / name, folder / name)
    shutil.copy(PACKAGING / "systemd" / "ovigyan-connector.service", folder)
    (folder / "ovigyan-connector").write_text("#!/bin/sh\necho fake connector\n")
    return folder


def run(script, *args, check=False):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, check=check)


def test_install_places_the_executable_and_the_unit(kit, tmp_path):
    root = tmp_path / "root"
    result = run(kit / "install.sh", "--prefix", str(root), "--no-systemctl", "--no-user")
    assert result.returncode == 0, result.stderr
    binary = root / "usr/local/bin/ovigyan-connector"
    unit = root / "etc/systemd/system/ovigyan-connector.service"
    assert binary.read_text().endswith("fake connector\n") and stat.S_IMODE(binary.stat().st_mode) == 0o755
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644
    assert "ovigyan-connector open" in result.stdout and "ssh -L 47890" in result.stdout


def test_running_it_twice_is_fine_and_upgrades_the_executable(kit, tmp_path):
    root = tmp_path / "root"
    run(kit / "install.sh", "--prefix", str(root), "--no-systemctl", "--no-user", check=True)
    (kit / "ovigyan-connector").write_text("#!/bin/sh\necho new version\n")
    assert run(kit / "install.sh", "--prefix", str(root), "--no-systemctl", "--no-user").returncode == 0
    assert (root / "usr/local/bin/ovigyan-connector").read_text().endswith("new version\n")


def test_the_unit_runs_the_service_with_the_page_and_a_private_state_folder():
    unit = (PACKAGING / "systemd" / "ovigyan-connector.service").read_text()
    assert "ExecStart=/usr/local/bin/ovigyan-connector run" in unit
    assert "Environment=OVIGYAN_CONNECTOR_HOME=/var/lib/ovigyan-connector" in unit
    assert "StateDirectoryMode=0700" in unit and "User=ovigyan-connector" in unit
    assert "EnvironmentFile" not in unit  # no more hand-edited settings file: the page and the cloud hold them
    for hardening in ("NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true", "Restart=always"):
        assert hardening in unit


def test_the_installer_works_when_the_unit_sits_in_the_repo_layout(tmp_path):
    folder = tmp_path / "linux"
    folder.mkdir()
    shutil.copy(PACKAGING / "linux" / "install.sh", folder / "install.sh")
    (tmp_path / "systemd").mkdir()
    shutil.copy(PACKAGING / "systemd" / "ovigyan-connector.service", tmp_path / "systemd")
    (folder / "ovigyan-connector").write_text("#!/bin/sh\n")
    assert run(folder / "install.sh", "--prefix", str(tmp_path / "r"), "--no-systemctl", "--no-user").returncode == 0


def test_a_missing_executable_is_explained(kit, tmp_path):
    (kit / "ovigyan-connector").unlink()
    result = run(kit / "install.sh", "--prefix", str(tmp_path / "r"), "--no-systemctl", "--no-user")
    assert result.returncode == 1 and "next to the ovigyan-connector executable" in result.stderr
    assert not (tmp_path / "r").exists()


@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else True, reason="needs a non-root user")
def test_without_root_and_without_a_prefix_it_asks_for_sudo(kit):
    result = run(kit / "install.sh", "--no-systemctl", "--no-user")
    assert result.returncode == 1 and "sudo" in result.stderr


def test_unknown_options_are_refused(kit):
    assert run(kit / "install.sh", "--frobnicate").returncode == 2
    assert run(kit / "uninstall.sh", "--frobnicate").returncode == 2


def test_uninstall_removes_the_service_but_keeps_the_data(kit, tmp_path):
    root = tmp_path / "root"
    run(kit / "install.sh", "--prefix", str(root), "--no-systemctl", "--no-user", check=True)
    data = root / "var/lib/ovigyan-connector"
    data.mkdir(parents=True)
    (data / "connector.json").write_text("{}")
    result = run(kit / "uninstall.sh", "--prefix", str(root), "--no-systemctl")
    assert result.returncode == 0
    assert not (root / "usr/local/bin/ovigyan-connector").exists()
    assert not (root / "etc/systemd/system/ovigyan-connector.service").exists()
    assert (data / "connector.json").exists()


def test_uninstall_purge_removes_the_data_too(kit, tmp_path):
    root = tmp_path / "root"
    run(kit / "install.sh", "--prefix", str(root), "--no-systemctl", "--no-user", check=True)
    data = root / "var/lib/ovigyan-connector"
    data.mkdir(parents=True)
    (data / "connector.json").write_text("{}")
    assert run(kit / "uninstall.sh", "--prefix", str(root), "--no-systemctl", "--purge").returncode == 0
    assert not data.exists()


def test_uninstall_when_nothing_is_installed_is_harmless(kit, tmp_path):
    assert run(kit / "uninstall.sh", "--prefix", str(tmp_path / "empty"), "--no-systemctl").returncode == 0


def test_the_windows_installer_no_longer_asks_for_settings():
    iss = (PACKAGING / "windows" / "installer.iss").read_text()
    for gone in ("CreateInputQueryPage", "INGEST_URL", "DEVICE_HOST", "ovigyan_dev_", "connector.env"):
        assert gone not in iss
    assert "-ArgumentList '" in iss and "open'" in iss and "tray'" in iss  # shortcuts to the page and the status icon
    assert "postinstall" in iss  # offers to open the page when setup ends


def test_the_scheduled_task_never_times_out_and_the_home_folder_is_locked_down():
    script = (PACKAGING / "windows" / "register-task.ps1").read_text()
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script  # the default stops a task after 72 hours
    assert "S-1-5-18" in script and "S-1-5-32-544" in script  # SYSTEM and Administrators
    assert "S-1-5-32-545" in script  # Users: read the page token and the status note only
    run_script = (PACKAGING / "windows" / "run-connector.ps1").read_text()
    assert "run" in run_script and "ATTENDANCE_" not in run_script
