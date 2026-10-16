"""The school's own copy of its data, kept in a folder on this PC.

Ovigyan streams the copy (a ZIP of spreadsheet files) when the school has switched a nightly copy on for this PC. It is
downloaded to a temporary file, checked to be a whole, readable ZIP with its manifest, and only then given its final
name; the newest few are kept. A failed or partial download never replaces a good copy and is removed.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cloud import CloudClient, CloudError

PATTERN = "ovigyan-backup-*.zip"
KEEP = 14
MIN_FREE_BYTES = 200 * 1024 * 1024


class BackupError(Exception):
    """Why a copy was not taken, in words for whoever looks after this PC."""


@dataclass
class BackupResult:
    path: Path
    bytes: int


def check_folder(folder: Path) -> Path:
    """The folder exists (made if needed) and can be written to. Raises BackupError."""
    if not folder.is_absolute():
        raise BackupError("Give the full path of the folder, for example D:\\SchoolBackups.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".ovigyan-write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as error:
        raise BackupError(f"Can't write to {folder}: {error.strerror or error}.") from None
    return folder


def verify(path: Path) -> dict:
    """The file is a whole ZIP with the manifest and README Ovigyan puts in it. Returns the manifest. Raises BackupError."""
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise BackupError(f"The copy is damaged ({bad} did not read back correctly), so it was not kept.")
            names = set(archive.namelist())
            if not {"manifest.json", "README.txt"} <= names:
                raise BackupError("That does not look like a copy from Ovigyan, so it was not kept.")
            manifest = json.loads(archive.read("manifest.json"))
    except zipfile.BadZipFile:
        raise BackupError("The copy did not arrive whole, so it was not kept. It will be tried again.") from None
    except (ValueError, KeyError):
        raise BackupError("The copy's manifest could not be read, so it was not kept.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise BackupError("The copy is in a newer format than this connector understands. Update the connector.")
    return manifest


def prune(folder: Path, keep: int = KEEP) -> list[Path]:
    """Remove all but the newest `keep` copies. Only files named like ours are ever touched."""
    copies = sorted(folder.glob(PATTERN), key=lambda path: path.name, reverse=True)  # the name holds the date
    removed = copies[keep:]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def run_backup(client: CloudClient, folder: Path, *, keep: int = KEEP, now: datetime | None = None, last_bytes: int = 0) -> BackupResult:
    """Take one copy. Raises BackupError (or CloudError when the site refuses or cannot be reached)."""
    folder = check_folder(Path(folder))
    needed = max(MIN_FREE_BYTES, int(last_bytes * 1.5))
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise BackupError(f"There is not enough free space in {folder} ({free // 1024**2} MB free, about {needed // 1024**2} MB needed).")
    for leftover in folder.glob(".ovigyan-backup-*.part"):
        leftover.unlink(missing_ok=True)  # an earlier attempt that was cut off
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M")
    part = folder / f".ovigyan-backup-{stamp}.part"
    final = folder / f"ovigyan-backup-{stamp}.zip"
    try:
        size = client.download_backup(part)
        verify(part)
        part.replace(final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    prune(folder, keep)
    return BackupResult(final, size)
