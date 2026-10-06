"""Keeps the connector on the version its Ovigyan site was tested with, with nobody having to do anything.

The site says which version it wants (and the oldest it still works with). When the connector is behind it
fetches that release's manifest, checks the manifest's signature against the key built into the program, checks
every file against the manifest, runs the new build's self-test, and only then installs it:
  * Windows: the signed setup program runs as a one-off SYSTEM task (outside this task's process tree, which the
    installer ends), exactly like a manual upgrade; settings and pairing are kept.
  * Linux: the verified build is staged and the service restarts; systemd's root-only ExecStartPre
    (`ovigyan-connector apply-update`) verifies it again and swaps the executable.
It never installs a version other than the one the site asked for, never an older one, and tries at most once an hour.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .update_key import PUBLIC_KEY

RELEASES = "https://github.com/infinio-labs/ovigyan-connector/releases/download"
MAX_MANIFEST = 256 * 1024
MAX_DOWNLOAD = 250 * 1024 * 1024
RETRY_AFTER_SECONDS = 3600
UPDATE_TASK = "Ovigyan Connector Update"
LINUX_BINARY = "ovigyan-connector"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def parse_version(text: object) -> tuple[int, int, int] | None:
    return tuple(int(part) for part in text.split(".")) if isinstance(text, str) and _VERSION.match(text) else None  # type: ignore[return-value]


@dataclass
class Policy:
    """What the site asks for: a version to run, the oldest it tolerates, and whether updates are allowed."""

    target: str | None = None
    minimum: str | None = None
    auto: bool = True


@dataclass
class Outcome:
    status: str  # up_to_date | held | disabled | waiting | applying | restart | failed
    message: str


def verify_signature(data: bytes, signature_b64: str, public_key_b64: str = PUBLIC_KEY) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True)).verify(
            base64.b64decode(signature_b64.strip(), validate=True), data
        )
        return True
    except Exception:  # a bad key, a bad signature or a missing library all mean "do not trust this"
        return False


def _download(url: str, limit: int, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download is larger than allowed")
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _platform() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


class Updater:
    def __init__(
        self,
        home: Path,
        *,
        base_url: str | None = None,
        public_key: str | None = None,
        fetch: Callable[[str, int], bytes] = _download,
        platform_name: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.home = Path(home)
        self.dir = self.home / "updates"
        testing = os.environ.get("OVIGYAN_ALLOW_INSECURE_HTTP", "").strip().lower() == "true"
        # The trust anchor and release address can be redirected only in an explicit test setup.
        self.base_url = (base_url or (testing and os.environ.get("OVIGYAN_UPDATE_BASE_URL")) or RELEASES).rstrip("/")
        self.public_key = public_key or (testing and os.environ.get("OVIGYAN_UPDATE_PUBLIC_KEY")) or PUBLIC_KEY
        self.fetch = fetch
        self.platform = platform_name or _platform()
        self.runner = runner

    # ── state ──────────────────────────────────────────────────────────────────────────────────────
    @property
    def state_path(self) -> Path:
        return self.home / "update.json"

    def state(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, **changes: object) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({**self.state(), **changes}, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ── deciding ───────────────────────────────────────────────────────────────────────────────────
    def run(self, policy: Policy, *, idle: bool = True, now: float | None = None, force: bool = False, allow_same: bool = False) -> Outcome:
        """One look at whether to update, and the update itself when it is time. Never raises."""
        now = time.time() if now is None else now
        target, current = parse_version(policy.target), parse_version(__version__)
        if not target or not current or (target < current if allow_same else target <= current):
            return Outcome("up_to_date", "This connector is up to date.")
        if not policy.auto and not force:
            return Outcome("held", f"Version {policy.target} is available; automatic updates are off for this site.")
        if not self.public_key:
            return Outcome("disabled", "This build has no update key, so it can't update itself.")
        if self.platform not in {"windows", "linux"}:
            return Outcome("disabled", "Automatic updates are available on Windows and Linux.")
        urgent = (parse_version(policy.minimum) or (0, 0, 0)) > current
        if not idle and not urgent and not force:
            return Outcome("waiting", "Waiting for the queue to empty before updating.")
        state = self.state()
        if not force and state.get("target") == policy.target and now - float(state.get("attempted_at", 0)) < RETRY_AFTER_SECONDS:
            return Outcome("waiting", "An update was tried recently; trying again later.")
        self._save(target=policy.target, attempted_at=now)
        try:
            outcome = self._update(policy.target or "")
        except Exception as error:  # noqa: BLE001 - an update must never take the connector down
            self.cleanup()
            outcome = Outcome("failed", f"Could not update to {policy.target}: {error}")
        self._save(status=outcome.status, message=outcome.message)
        return outcome

    # ── fetching and checking ───────────────────────────────────────────────────────────────────────
    def manifest(self, version: str) -> dict:
        base = f"{self.base_url}/v{version}"
        data = self.fetch(f"{base}/manifest.json", MAX_MANIFEST)
        if not verify_signature(data, self.fetch(f"{base}/manifest.json.sig", 4096).decode("ascii", "ignore"), self.public_key):
            raise ValueError("the release signature does not match; refusing it")
        manifest = json.loads(data)
        if manifest.get("version") != version or not isinstance(manifest.get("files"), dict):
            raise ValueError("the signed manifest is for a different version")
        return manifest

    def _asset(self, version: str, manifest: dict, name: str) -> bytes:
        expected = manifest["files"].get(name)
        if not isinstance(expected, str):
            raise ValueError(f"{name} is not part of release {version}")
        data = self.fetch(f"{self.base_url}/v{version}/{name}", MAX_DOWNLOAD)
        if _sha256(data) != expected:
            raise ValueError(f"{name} does not match the signed checksum")
        return data

    def _self_test(self, path: Path, version: str) -> None:
        done = self.runner([str(path), "self-test"], capture_output=True, text=True, timeout=90)
        if done.returncode != 0 or f"ovigyan-connector {version} ok" not in done.stdout:
            raise ValueError("the new build did not start on this computer")

    def _update(self, version: str) -> Outcome:
        manifest = self.manifest(version)
        self.cleanup()
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.platform == "windows":
            exe = self.dir / "ovigyan-connector.exe"
            exe.write_bytes(self._asset(version, manifest, "ovigyan-connector.exe"))
            self._self_test(exe, version)
            setup_name = f"ovigyan-connector-setup-{version}.exe"
            setup = self.dir / setup_name
            setup.write_bytes(self._asset(version, manifest, setup_name))
            self._run_installer(setup)
            return Outcome("applying", f"Installing version {version}.")
        if self.platform == "linux":
            binary = self.dir / LINUX_BINARY
            binary.write_bytes(self._asset(version, manifest, LINUX_BINARY))
            binary.chmod(0o755)
            self._self_test(binary, version)
            (self.dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (self.dir / "manifest.json.sig").write_bytes(self.fetch(f"{self.base_url}/v{version}/manifest.json.sig", 4096))
            return Outcome("restart", f"Version {version} is ready; restarting to switch to it.")
        return Outcome("failed", "Automatic updates are not available on this system.")

    def _run_installer(self, setup: Path) -> None:
        """Run the setup as a one-off SYSTEM task so it survives the installer ending this task's process tree."""
        command = f'"{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
        for arguments in (
            ["schtasks.exe", "/Create", "/TN", UPDATE_TASK, "/SC", "ONCE", "/ST", "00:00", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F", "/TR", command],
            ["schtasks.exe", "/Run", "/TN", UPDATE_TASK],
        ):
            done = self.runner(arguments, capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise ValueError(f"could not start the installer ({(done.stderr or done.stdout).strip()[:200]})")

    def cleanup(self) -> None:
        """Remove files from an earlier attempt (the setup is not needed once it has run)."""
        if self.dir.is_dir():
            for path in self.dir.iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)


def apply_staged(home: Path, install_path: Path, public_key: str = PUBLIC_KEY) -> str:
    """Linux, run as root before the service starts: swap in the staged build if it checks out. Returns what happened."""
    staged = Path(home) / "updates"
    binary, manifest_path, sig_path = staged / LINUX_BINARY, staged / "manifest.json", staged / "manifest.json.sig"
    if not binary.exists():
        return "nothing staged"
    try:
        if any(path.is_symlink() or not path.is_file() for path in (binary, manifest_path, sig_path)):
            raise ValueError("staged files are not regular files")
        manifest_bytes = manifest_path.read_bytes()
        if not public_key or not verify_signature(manifest_bytes, sig_path.read_text(encoding="ascii"), public_key):
            raise ValueError("signature does not match")
        manifest = json.loads(manifest_bytes)
        data = binary.read_bytes()
        new, current = parse_version(manifest.get("version")), parse_version(__version__)
        if not new or not current or new <= current:
            raise ValueError("staged build is not newer than this one")
        if manifest["files"].get(LINUX_BINARY) != _sha256(data):
            raise ValueError("staged build does not match the signed checksum")
        temp = install_path.with_name(install_path.name + ".new")
        temp.write_bytes(data)
        temp.chmod(0o755)
        if install_path.exists():
            os.replace(install_path, install_path.with_name(install_path.name + ".previous"))
        os.replace(temp, install_path)
        return f"installed {manifest['version']}"
    except Exception as error:  # noqa: BLE001 - leave the working executable alone
        return f"refused staged update: {error}"
    finally:
        for path in (binary, manifest_path, sig_path):
            path.unlink(missing_ok=True)
