"""Uploaded file storage and the 90-day retention purge."""

import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.cli import uploads as uploads_cli
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.models.identity import Organization
from app.models.imports import DataImport, DataImportRow
from app.services.storage import (
    FileTooLargeError,
    InvalidStorageKeyError,
    check_key,
    get_file_storage,
    storage_key,
)
from app.services.uploads import purge_expired_files

CSV = "Date,Total\n28/09/2026,£12.00\n".encode()
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def new_key(source="csv"):
    return storage_key(uuid.uuid4(), uuid.uuid4(), source)


# --- keys ---------------------------------------------------------------------------------------


def test_keys_are_one_folder_per_business():
    org, imp = uuid.uuid4(), uuid.uuid4()
    assert storage_key(org, imp, "csv") == f"{org}/{imp}.csv"
    assert storage_key(org, imp, "excel") == f"{org}/{imp}.xlsx"


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        f"{uuid.uuid4()}/../{uuid.uuid4()}.csv",
        f"/{uuid.uuid4()}/{uuid.uuid4()}.csv",
        f"{uuid.uuid4()}\\{uuid.uuid4()}.csv",
        f"C:/{uuid.uuid4()}.csv",
        f"{uuid.uuid4()}/{uuid.uuid4()}.exe",
        f"{uuid.uuid4()}/{uuid.uuid4()}.csv/..",
        f"{uuid.uuid4()}/till export.csv",  # the user's own filename is never a key
        f"{str(uuid.uuid4()).upper()}/{uuid.uuid4()}.csv",
        f"{uuid.uuid4()}/{uuid.uuid4()}.csv\n",  # a bare `$` would let a trailing newline through
        "",
    ],
)
def test_anything_but_a_generated_key_is_refused(storage, key):
    with pytest.raises(InvalidStorageKeyError):
        check_key(key)
    for action in (storage.open, storage.exists, storage.delete):
        with pytest.raises(InvalidStorageKeyError):
            action(key)
    with pytest.raises(InvalidStorageKeyError):
        storage.save(key, io.BytesIO(CSV), max_bytes=100)


# --- saving and reading -------------------------------------------------------------------------


def test_save_read_delete(storage):
    key = new_key()
    stored = storage.save(key, io.BytesIO(CSV), max_bytes=1000)
    assert stored.key == key
    assert stored.size_bytes == len(CSV)
    assert stored.sha256 == hashlib.sha256(CSV).hexdigest()
    assert storage.exists(key)
    with storage.open(key) as f:
        assert f.read() == CSV
    assert storage.delete(key) is True
    assert not storage.exists(key)
    assert storage.delete(key) is False  # already gone is fine


def test_files_land_inside_the_storage_folder(storage):
    key = new_key("excel")
    storage.save(key, io.BytesIO(b"PK"), max_bytes=10)
    assert (storage.root / key).is_file()


def test_large_files_are_streamed_in_chunks(storage):
    data = b"x" * (3 * 1024 * 1024 + 7)
    stored = storage.save(new_key(), io.BytesIO(data), max_bytes=len(data))
    assert stored.size_bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()


def test_too_large_is_refused_and_nothing_is_left_behind(storage):
    key = new_key()
    with pytest.raises(FileTooLargeError) as caught:
        storage.save(key, io.BytesIO(b"x" * 101), max_bytes=100)
    assert caught.value.max_bytes == 100
    assert not storage.exists(key)
    assert list(storage.root.rglob("*.*")) == []  # no .partial file either


def test_exactly_the_limit_is_allowed(storage):
    stored = storage.save(new_key(), io.BytesIO(b"x" * 100), max_bytes=100)
    assert stored.size_bytes == 100


def test_a_failed_read_leaves_nothing_behind(storage):
    class Broken(io.RawIOBase):
        def read(self, n=-1):
            raise OSError("connection dropped")

    key = new_key()
    with pytest.raises(OSError, match="connection dropped"):
        storage.save(key, Broken(), max_bytes=100)
    assert list(storage.root.rglob("*.*")) == []


def test_an_existing_file_is_never_overwritten(storage):
    key = new_key()
    storage.save(key, io.BytesIO(CSV), max_bytes=1000)
    with pytest.raises(FileExistsError):
        storage.save(key, io.BytesIO(b"other"), max_bytes=1000)
    with storage.open(key) as f:
        assert f.read() == CSV


def test_the_default_storage_uses_the_configured_folder(monkeypatch, tmp_path):
    from app.services import storage as storage_module

    class FakeSettings:
        upload_dir = str(tmp_path / "configured")

    monkeypatch.setattr(storage_module, "get_settings", lambda: FakeSettings)
    assert get_file_storage().root == (tmp_path / "configured").resolve()


def test_the_default_upload_folder_is_outside_the_repo():
    from app.core.config import Settings

    repo = Path(__file__).resolve().parents[2]
    upload_dir = Path(Settings(_env_file=None).upload_dir).resolve()
    assert not upload_dir.is_relative_to(repo)


def test_tests_cannot_reach_the_real_upload_folder(app):
    with pytest.raises(RuntimeError, match="real upload folder"):
        app.dependency_overrides[get_file_storage]()


# --- 90-day retention ---------------------------------------------------------------------------


@pytest.fixture
def uploads(db, storage):
    """Two businesses' uploads at different ages, each with its file on disk."""
    a, b = Organization(name="A Ltd"), Organization(name="B Ltd")
    db.add_all([a, b])
    db.flush()

    def upload(org, days_old, *, on_disk=True):
        with tenant_scope(db, org.id):
            imp = DataImport(source="csv", dataset="sales", original_filename="f.csv")
            imp.id = uuid.uuid4()
            imp.storage_key = storage_key(org.id, imp.id, "csv")
            stored = (
                storage.save(imp.storage_key, io.BytesIO(CSV), max_bytes=1000) if on_disk else None
            )
            imp.file_size_bytes = len(CSV)
            imp.file_sha256 = stored.sha256 if stored else "0" * 64
            imp.created_at = NOW - timedelta(days=days_old)
            db.add(imp)
            db.flush()
            db.add(DataImportRow(import_id=imp.id, row_number=2, raw={"Total": "£12.00"}))
            db.flush()
            return imp

    return {
        "a_old": upload(a, 120),
        "b_old": upload(b, 91),
        "a_recent": upload(a, 89),
        "b_new": upload(b, 0),
        "a_old_missing": upload(a, 100, on_disk=False),
    }


def reloaded(db, imp):
    """Fresh from the database. Purge is a cross-business job, so its tests are too."""
    return db.get(DataImport, imp.id, populate_existing=True, execution_options=ACROSS_TENANTS)


def raw_rows(db, imp):
    return db.scalars(
        select(DataImportRow.raw)
        .where(DataImportRow.import_id == imp.id)
        .execution_options(**ACROSS_TENANTS)
    ).all()


def test_purge_deletes_files_older_than_90_days_for_every_business(db, storage, uploads):
    report = purge_expired_files(db, storage, now=NOW, retention_days=90)
    assert (report.expired, report.files_deleted) == (3, 2)

    for name in ("a_old", "b_old", "a_old_missing"):
        imp = uploads[name]
        imp = reloaded(db, imp)
        assert imp.file_deleted_at == NOW
        assert not storage.exists(imp.storage_key)
        assert raw_rows(db, imp) == [{}]  # file contents gone from the rows too
    for name in ("a_recent", "b_new"):
        imp = uploads[name]
        imp = reloaded(db, imp)
        assert imp.file_deleted_at is None
        assert storage.exists(imp.storage_key)
        assert raw_rows(db, imp) == [{"Total": "£12.00"}]


def test_purge_keeps_the_import_record(db, storage, uploads):
    purge_expired_files(db, storage, now=NOW, retention_days=90)
    count = db.scalar(
        select(func.count()).select_from(DataImport).execution_options(**ACROSS_TENANTS)
    )
    assert count == 5


def test_purge_runs_once_per_file(db, storage, uploads):
    purge_expired_files(db, storage, now=NOW, retention_days=90)
    again = purge_expired_files(db, storage, now=NOW + timedelta(hours=1), retention_days=90)
    assert (again.expired, again.files_deleted) == (0, 0)
    assert reloaded(db, uploads["a_old"]).file_deleted_at == NOW  # first purge time kept


def test_dry_run_changes_nothing(db, storage, uploads):
    report = purge_expired_files(db, storage, now=NOW, retention_days=90, dry_run=True)
    assert (report.expired, report.files_deleted) == (3, 2)
    for imp in uploads.values():
        imp = reloaded(db, imp)
        assert imp.file_deleted_at is None
    assert storage.exists(uploads["a_old"].storage_key)


def test_retention_comes_from_settings_by_default(db, storage, uploads, monkeypatch):
    from app.services import uploads as uploads_module

    class FakeSettings:
        upload_retention_days = 30

    monkeypatch.setattr(uploads_module, "get_settings", lambda: FakeSettings)
    assert purge_expired_files(db, storage, now=NOW, dry_run=True).expired == 4


def test_purge_command(db, storage, uploads, monkeypatch, capsys):
    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(uploads_cli, "get_sessionmaker", lambda: Session)
    monkeypatch.setattr(uploads_cli, "get_file_storage", lambda: storage)
    monkeypatch.setattr(
        uploads_cli,
        "purge_expired_files",
        lambda db_, storage_, dry_run: purge_expired_files(
            db_, storage_, now=NOW, retention_days=90, dry_run=dry_run
        ),
    )
    monkeypatch.setattr(db, "commit", lambda: None)  # keep the test transaction

    assert uploads_cli.main(["purge", "--dry-run"]) == 0
    assert (
        "Would purge 3 upload(s) older than 90 days (2 file(s) on disk)." in capsys.readouterr().out
    )
    assert storage.exists(uploads["a_old"].storage_key)

    assert uploads_cli.main(["purge"]) == 0
    assert "Purged 3 upload(s) older than 90 days (2 file(s) deleted)." in capsys.readouterr().out
    assert not storage.exists(uploads["a_old"].storage_key)
