from datetime import datetime

import pytest

from fake_cloud import PAIR_KEY, FakeCloud, make_backup_zip
from ovigyan_connector import backup as backup_module
from ovigyan_connector.backup import BackupError, check_folder, prune, run_backup, verify
from ovigyan_connector.cloud import CloudClient, CloudError

NOW = datetime(2026, 10, 16, 1, 30)


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


@pytest.fixture
def client(cloud):
    from fake_cloud import CREDENTIAL

    return CloudClient(cloud.url, CREDENTIAL, max_retries=0, retry_backoff_seconds=0)


def names(folder):
    return sorted(path.name for path in folder.iterdir())


def test_a_copy_is_downloaded_checked_and_given_its_final_name(client, tmp_path):
    result = run_backup(client, tmp_path, now=NOW)
    assert result.path == tmp_path / "ovigyan-backup-2026-10-16-0130.zip"
    assert result.bytes == result.path.stat().st_size == len(make_backup_zip())
    assert names(tmp_path) == ["ovigyan-backup-2026-10-16-0130.zip"]  # no temporary file left behind
    assert verify(result.path)["format"] == 1


def test_only_the_newest_fourteen_are_kept_and_nothing_else_is_touched(client, tmp_path):
    for day in range(1, 17):
        (tmp_path / f"ovigyan-backup-2026-09-{day:02d}-0100.zip").write_bytes(b"old")
    (tmp_path / "notes.txt").write_text("mine")
    (tmp_path / "holiday photos.zip").write_bytes(b"mine too")
    run_backup(client, tmp_path, now=NOW)
    kept = [n for n in names(tmp_path) if n.startswith("ovigyan-backup-")]
    assert len(kept) == 14 and kept[-1] == "ovigyan-backup-2026-10-16-0130.zip"
    assert "ovigyan-backup-2026-09-01-0100.zip" not in kept  # the oldest went
    assert "notes.txt" in names(tmp_path) and "holiday photos.zip" in names(tmp_path)


@pytest.mark.parametrize(
    "payload, reason",
    [
        (b"this is not a zip at all", "did not arrive whole"),
        (make_backup_zip()[:200], "did not arrive whole"),
        (make_backup_zip(with_manifest=False), "does not look like a copy from Ovigyan"),
        (make_backup_zip(format_version=2), "newer format"),
    ],
)
def test_a_copy_that_does_not_check_out_is_never_kept_and_never_replaces_a_good_one(client, cloud, tmp_path, payload, reason):
    run_backup(client, tmp_path, now=datetime(2026, 10, 15, 1, 30))
    good = names(tmp_path)
    cloud.backup_bytes = payload
    with pytest.raises(BackupError, match=reason):
        run_backup(client, tmp_path, now=NOW)
    assert names(tmp_path) == good  # the earlier good copy is all that is there; no partial file


def test_the_site_refusing_or_being_busy_is_explained(client, cloud, tmp_path):
    cloud.backup_status, cloud.backup_error = 403, "A nightly copy to this PC is not switched on."
    with pytest.raises(CloudError) as off:
        run_backup(client, tmp_path)
    assert off.value.kind == "backup_off" and "not switched on" in off.value.message
    cloud.backup_status, cloud.backup_error = 429, "Another copy is being prepared."
    with pytest.raises(CloudError) as busy:
        run_backup(client, tmp_path)
    assert busy.value.kind == "backup_busy"
    assert names(tmp_path) == []


def test_too_little_free_space_stops_before_anything_is_downloaded(client, cloud, tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**12, 10**12 - 1000, 1000))
    before = len(cloud.requests)
    with pytest.raises(BackupError, match="not enough free space"):
        run_backup(client, tmp_path)
    assert len(cloud.requests) == before and names(tmp_path) == []


def test_the_space_needed_grows_with_the_last_copy(client, tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**12, 0, 500 * 1024**2))
    run_backup(client, tmp_path, now=NOW, last_bytes=100 * 1024**2)  # 150 MB needed: fine
    with pytest.raises(BackupError, match="not enough free space"):
        run_backup(client, tmp_path, now=datetime(2026, 10, 17, 1, 30), last_bytes=2 * 1024**3)


def test_a_cut_off_earlier_attempt_is_cleaned_up(client, tmp_path):
    (tmp_path / ".ovigyan-backup-2026-10-14-0100.part").write_bytes(b"half")
    run_backup(client, tmp_path, now=NOW)
    assert not list(tmp_path.glob("*.part"))


class TestFolder:
    def test_a_relative_path_is_refused_because_the_service_does_not_run_where_you_think(self, tmp_path):
        with pytest.raises(BackupError, match="full path"):
            check_folder(__import__("pathlib").Path("backups"))

    def test_a_folder_is_made_when_it_is_missing(self, tmp_path):
        assert check_folder(tmp_path / "a" / "b").is_dir()

    def test_a_folder_that_cannot_be_written_is_explained(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("a file, not a folder")
        with pytest.raises(BackupError, match="Can't write to"):
            check_folder(blocker / "inside")


def test_prune_orders_by_the_date_in_the_name_not_the_file_time(tmp_path):
    for day in (1, 2, 3):
        (tmp_path / f"ovigyan-backup-2026-10-0{day}-0100.zip").write_bytes(b"x")
    removed = prune(tmp_path, keep=2)
    assert [p.name for p in removed] == ["ovigyan-backup-2026-10-01-0100.zip"]
    assert backup_module.PATTERN == "ovigyan-backup-*.zip"
