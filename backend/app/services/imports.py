"""Uploading a data file and reading what's in it (Phase 4 step 3).

An upload is stored, checked and counted, and becomes a `data_imports` row with status
"uploaded". Column mapping (step 4), validation (step 5) and the import itself (step 6)
build on it. The session must be scoped to the organisation (CurrentTenant).

If anything goes wrong the stored file is deleted again, so a refused upload leaves no
trace on disk or in the database.
"""

import re
import uuid
from pathlib import PurePosixPath
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.imports import FILE_SUFFIXES, DataImport
from app.schemas.imports import (
    EarlierUploadOut,
    ImportDetailOut,
    ImportOut,
    ImportPatch,
    ImportUploadedOut,
    PreviewOut,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.file_reader import FilePreview, UnreadableFileError, inspect_file
from app.services.storage import FileStorage, FileTooLargeError, storage_key

SOURCE_BY_SUFFIX = {suffix: source for source, suffix in FILE_SUFFIXES.items()}
EDITABLE = ("uploaded", "mapped", "validated")  # before the import has started
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(name: str | None) -> str:
    """The name to show the user: no folders, no control characters, at most 255 characters.
    Never used as a path on disk (files are stored under generated keys)."""
    base = PurePosixPath((name or "").replace("\\", "/")).name
    base = _CONTROL.sub("", base).strip()
    if len(base) > 255:
        stem, dot, suffix = base.rpartition(".")
        base = stem[: 255 - len(suffix) - 1] + dot + suffix
    return base


def source_for(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    try:
        return SOURCE_BY_SUFFIX[suffix]
    except KeyError:
        hint = " Save it as an Excel Workbook (.xlsx) first." if suffix == ".xls" else ""
        raise UnreadableFileError(
            f"Only .csv and .xlsx files can be uploaded.{hint}", code="unsupported_file_type"
        ) from None


def _preview_out(preview: FilePreview) -> PreviewOut:
    return PreviewOut(
        headers=preview.headers,
        sample_rows=preview.sample_rows,
        row_count=preview.row_count,
        sheets=preview.sheets,
        needs_sheet=preview.needs_sheet,
        encoding=preview.encoding,
        delimiter=preview.delimiter,
    )


def _apply_counts(data_import: DataImport, preview: FilePreview) -> None:
    data_import.row_count = preview.row_count
    data_import.valid_count = data_import.invalid_count = data_import.duplicate_count = 0
    data_import.sheet_name = preview.sheet_name


def upload_import(
    db: Session,
    storage: FileStorage,
    tenant,
    *,
    stream: BinaryIO,
    filename: str | None,
    dataset: str,
    meta: RequestMeta,
) -> ImportUploadedOut:
    settings = get_settings()
    filename = clean_filename(filename)
    if not filename:
        raise UnreadableFileError("The upload has no file name.", code="unsupported_file_type")
    source = source_for(filename)

    import_id = uuid.uuid4()
    key = storage_key(tenant.organization_id, import_id, source)
    try:
        stored = storage.save(key, stream, max_bytes=settings.max_upload_bytes)
    except FileTooLargeError as exc:
        raise AppError(
            f"That file is larger than {exc.max_bytes // (1024 * 1024)} MB.",
            code="file_too_large",
            status_code=413,
        ) from None

    try:
        with storage.open(key) as saved:
            preview = inspect_file(saved, source, max_rows=settings.max_upload_rows)
        earlier = db.scalar(
            select(DataImport)
            .where(DataImport.file_sha256 == stored.sha256)
            .order_by(DataImport.created_at.desc())
            .limit(1)
        )
        data_import = DataImport(
            id=import_id,
            source=source,
            dataset=dataset,
            original_filename=filename,
            storage_key=key,
            file_size_bytes=stored.size_bytes,
            file_sha256=stored.sha256,
            uploaded_by_user_id=tenant.user.id,
        )
        _apply_counts(data_import, preview)
        db.add(data_import)
        db.flush()
        record_audit(
            db,
            AuditAction.IMPORT_UPLOADED,
            actor_user_id=tenant.user.id,
            organization_id=tenant.organization_id,
            target_type="data_import",
            target_id=import_id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={
                "dataset": dataset,
                "source": source,
                "size_bytes": stored.size_bytes,
                "rows": preview.row_count,
                "same_file_as": str(earlier.id) if earlier else None,
            },
        )
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(key)  # a refused upload leaves nothing behind
        raise

    db.refresh(data_import)
    return ImportUploadedOut(
        **ImportOut.model_validate(data_import).model_dump(),
        preview=_preview_out(preview),
        duplicate_of=EarlierUploadOut.model_validate(earlier, from_attributes=True)
        if earlier
        else None,
    )


def _get(db: Session, import_id: uuid.UUID) -> DataImport:
    data_import = db.get(DataImport, import_id)  # scoped: another business's id is "not found"
    if data_import is None:
        raise NotFoundError("Import not found")
    return data_import


def _read_preview(
    storage: FileStorage, data_import: DataImport, *, count: bool
) -> FilePreview | None:
    if data_import.file_deleted_at is not None or not storage.exists(data_import.storage_key):
        return None
    with storage.open(data_import.storage_key) as f:
        return inspect_file(
            f,
            data_import.source,
            sheet_name=data_import.sheet_name,
            header_row=data_import.header_row,
            max_rows=get_settings().max_upload_rows,
            count=count,
        )


def list_imports(db: Session, limit: int = 50) -> list[ImportOut]:
    rows = db.scalars(select(DataImport).order_by(DataImport.created_at.desc()).limit(limit))
    return [ImportOut.model_validate(r) for r in rows]


def get_import(db: Session, storage: FileStorage, import_id: uuid.UUID) -> ImportDetailOut:
    data_import = _get(db, import_id)
    preview = _read_preview(storage, data_import, count=False)
    if preview is not None and not preview.needs_sheet:
        preview.row_count = data_import.row_count  # counted at upload; don't re-count
    return ImportDetailOut(
        **ImportOut.model_validate(data_import).model_dump(),
        preview=_preview_out(preview) if preview else None,
    )


def update_import(
    db: Session,
    storage: FileStorage,
    tenant,
    import_id: uuid.UUID,
    body: ImportPatch,
    meta: RequestMeta,
) -> ImportDetailOut:
    """Choose the Excel sheet and/or the header row, then re-read the file."""
    data_import = _get(db, import_id)
    if data_import.status not in EDITABLE:
        raise ConflictError(
            "This import has already run, so it can't be changed", code="not_editable"
        )
    if data_import.file_deleted_at is not None:
        raise ConflictError("The original file has been deleted", code="file_deleted")
    changes = body.model_dump(include=body.model_fields_set, exclude_none=True)
    if "sheet_name" in changes and data_import.source != "excel":
        raise UnreadableFileError("Only Excel files have sheets.", code="sheet_only_for_excel")

    sheet_name = changes.get("sheet_name", data_import.sheet_name)
    header_row = changes.get("header_row", data_import.header_row)
    if not storage.exists(data_import.storage_key):
        raise ConflictError("The original file is missing", code="file_deleted")
    with storage.open(data_import.storage_key) as f:
        preview = inspect_file(
            f,
            data_import.source,
            sheet_name=sheet_name,
            header_row=header_row,
            max_rows=get_settings().max_upload_rows,
        )

    data_import.header_row = header_row
    _apply_counts(data_import, preview)
    data_import.column_mapping = {}  # a different sheet or header row means different columns
    data_import.status = "uploaded"
    record_audit(
        db,
        AuditAction.IMPORT_UPDATED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_import",
        target_id=import_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details=changes,
    )
    db.commit()
    db.refresh(data_import)
    return ImportDetailOut(
        **ImportOut.model_validate(data_import).model_dump(), preview=_preview_out(preview)
    )
