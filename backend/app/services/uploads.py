"""Retention of uploaded files (decision 2026-09-28: originals kept for 90 days).

After the retention period the original file is deleted and the raw copy of each row
(`data_import_rows.raw`, which holds the same content, possibly customer details) is
cleared. The import record, row statuses and the imported records themselves stay, so
history and undo keep working. Run daily in production:

    python -m app.cli.uploads purge
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.tenant import ACROSS_TENANTS
from app.models.imports import DataImport, DataImportRow
from app.services.storage import FileStorage

logger = logging.getLogger("vyterlix.uploads")


@dataclass(frozen=True)
class PurgeReport:
    expired: int  # imports past retention whose file hadn't been purged yet
    files_deleted: int  # of those, how many files were actually on disk


def purge_expired_files(
    db: Session,
    storage: FileStorage,
    *,
    now: datetime | None = None,
    retention_days: int | None = None,
    dry_run: bool = False,
) -> PurgeReport:
    """Deliberately spans every business: this is a system job, not a user request."""
    now = now or datetime.now(UTC)
    if retention_days is None:
        retention_days = get_settings().upload_retention_days
    cutoff = now - timedelta(days=retention_days)

    expired = db.scalars(
        select(DataImport)
        .where(DataImport.file_deleted_at.is_(None), DataImport.created_at < cutoff)
        .order_by(DataImport.created_at)
        .execution_options(**ACROSS_TENANTS)
    ).all()
    if dry_run:
        files = sum(storage.exists(i.storage_key) for i in expired)
        return PurgeReport(expired=len(expired), files_deleted=files)

    files_deleted = 0
    for data_import in expired:
        # Delete first: if the commit then fails, the next run finds the row again and
        # deleting an already-missing file is harmless.
        files_deleted += storage.delete(data_import.storage_key)
        data_import.file_deleted_at = now
    if expired:
        db.execute(
            update(DataImportRow)
            .where(DataImportRow.import_id.in_([i.id for i in expired]))
            .values(raw={})
            .execution_options(**ACROSS_TENANTS)
        )
    db.flush()
    logger.info(
        "uploads.purged",
        extra={"ctx": {"expired": len(expired), "files_deleted": files_deleted}},
    )
    return PurgeReport(expired=len(expired), files_deleted=files_deleted)
