"""Running an import and undoing it (Phase 4 step 6).

Importing creates the business's records from the rows that passed checking. It is all or
nothing: one database transaction, so a failure part-way leaves the data exactly as it was.
Undoing removes everything the import created, in dependency order, also all or nothing.

The session must be scoped to the organisation (CurrentTenant).
"""

import uuid
from collections import Counter
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement, Supplier
from app.models.imports import DataImport, DataImportRow, DataSource
from app.schemas.import_results import ImportResultOut, RecordsOut, UndoResultOut
from app.schemas.imports import ImportOut
from app.services import import_records as records
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.import_rows import parse_row
from app.services.import_validation import existing_keys

BATCH = 1000
# Children before parents, so nothing is deleted while something still points at it.
UNDO_ORDER = (StockMovement, Sale, Expense, Customer, Supplier, Product)


DATA_TABLES = (
    "sales",
    "sale_lines",
    "expenses",
    "stock_movements",
    "customers",
    "suppliers",
    "products",
)


def refresh_statistics(db: Session) -> None:
    """Tell the database how big the data tables now are (ANALYZE; fixed table names only).

    Right after a bulk import Postgres still believes these tables are nearly empty, and plans
    the lookups behind foreign keys and cascading deletes for that: with 50,000 rows an undo
    then took 8 minutes instead of 2 seconds. This also discards stale cached plans.
    """
    db.execute(text("ANALYZE " + ", ".join(DATA_TABLES)))


def _grown_enough_to_replan(batches: int) -> bool:
    """After batches 1, 4, 16, 64...: the tables have grown about fourfold since the last time.

    Postgres plans the foreign-key check behind every inserted row once, while the table is
    still tiny, and keeps that plan for the whole transaction. A plan that scans the table is
    fine at 1,000 rows and ruinous at 50,000, so the statistics are refreshed (which discards
    those cached plans) as the import grows.
    """
    return batches >= 1 and (batches & (batches - 1)) == 0 and batches.bit_length() % 2 == 1


def _locked(db: Session, import_id: uuid.UUID) -> DataImport:
    """The import, locked so two clicks (or two people) can't run it twice at once."""
    data_import = db.execute(
        select(DataImport).where(DataImport.id == import_id).with_for_update()
    ).scalar_one_or_none()
    if data_import is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("Import not found")
    return data_import


def run_import(
    db: Session,
    tenant,
    import_id: uuid.UUID,
    meta: RequestMeta,
    *,
    today: date | None = None,
) -> ImportResultOut:
    try:
        return _run(db, tenant, import_id, meta, today)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Something in this file clashes with data you already have, so nothing was imported. "
            "Check the file again.",
            code="import_conflict",
            details={"constraint": getattr(exc.orig.diag, "constraint_name", None)},
        ) from exc
    except BaseException:
        db.rollback()
        raise


def _run(db, tenant, import_id, meta, today) -> ImportResultOut:
    data_import = _locked(db, import_id)
    if data_import.status == "imported":
        raise ConflictError("This import has already been done", code="already_imported")
    if data_import.status != "validated":
        raise ConflictError("Check the data before importing it", code="not_validated")
    if data_import.file_deleted_at is not None:
        raise ConflictError(
            "The original file has been deleted, so this can't be imported. Upload it again.",
            code="file_deleted",
        )
    if data_import.valid_count == 0:
        raise ConflictError("There are no valid rows to import", code="nothing_to_import")

    dataset, mapping, options = data_import.dataset, data_import.column_mapping, data_import.options
    resolver = records.Resolver(db, tenant.organization_id, data_import.id, data_import.source)
    created: Counter[str] = resolver.created
    imported = skipped_duplicates = skipped_invalid = 0
    last_row = 0
    batches = 0

    while True:
        batch = db.scalars(
            select(DataImportRow)
            .where(
                DataImportRow.import_id == data_import.id,
                DataImportRow.status == "valid",
                DataImportRow.row_number > last_row,
            )
            .order_by(DataImportRow.row_number)
            .limit(BATCH)
        ).all()
        if not batch:
            break
        last_row = batch[-1].row_number

        outcomes = [parse_row(dataset, r.raw, mapping, options, today=today) for r in batch]
        keys = {o.key for o in outcomes if o.ok and o.key is not None}
        already = existing_keys(db, dataset, data_import.source, keys) if keys else set()

        to_build, rows = [], []
        for row, outcome in zip(batch, outcomes, strict=True):
            if not outcome.ok:  # e.g. a date that has since become "in the future"
                row.status, row.errors = "invalid", outcome.errors
                skipped_invalid += 1
            elif outcome.key in already:  # added by someone else since it was checked
                row.status = "duplicate"
                row.errors = [
                    {
                        "field": "reference",
                        "code": "already_imported",
                        "message": "This is already in your data.",
                    }
                ]
                skipped_duplicates += 1
            else:
                to_build.append(outcome)
                rows.append(row)

        if to_build:
            tables, targets = records.build(dataset, resolver, to_build)
            records.insert_records(db, tables, created)
            for row, (table, target_id) in zip(rows, targets, strict=True):
                row.status, row.target_table, row.target_id = "imported", table, target_id
            imported += len(rows)
        db.flush()
        for row in batch:
            db.expunge(row)  # keep memory flat on big files
        batches += 1
        if _grown_enough_to_replan(batches):
            refresh_statistics(db)

    now = datetime.now(UTC)
    data_import.status = "imported"
    data_import.imported_at = now
    data_import.imported_count = imported
    data_import.valid_count = imported
    data_import.invalid_count += skipped_invalid
    data_import.duplicate_count += skipped_duplicates
    if data_import.data_source_id is not None:
        db.execute(
            update(DataSource)
            .where(DataSource.id == data_import.data_source_id)
            .values(last_imported_at=now)
        )
    record_audit(
        db,
        AuditAction.IMPORT_COMPLETED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_import",
        target_id=data_import.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={
            "dataset": dataset,
            "imported": imported,
            "skipped_duplicates": skipped_duplicates,
            "skipped_invalid": skipped_invalid,
            "created": dict(created),
        },
    )
    db.commit()
    refresh_statistics(db)
    db.commit()
    db.refresh(data_import)
    return ImportResultOut(
        data_import=ImportOut.model_validate(data_import),
        created=dict(created),
        skipped_duplicates=skipped_duplicates,
        skipped_invalid=skipped_invalid,
    )


# --- what an import has in the data ------------------------------------------------


def count_records(db: Session, import_id: uuid.UUID) -> dict[str, int]:
    counts = {}
    for model in (Sale, Expense, Customer, Supplier, Product, StockMovement):
        counts[model.__tablename__] = db.scalar(
            select(func.count()).select_from(model).where(model.import_id == import_id)
        )
    counts["sale_lines"] = db.scalar(
        select(func.count()).select_from(SaleLine).where(SaleLine.import_id == import_id)
    )
    return {table: n for table, n in counts.items() if n}


def records_of(db: Session, import_id: uuid.UUID) -> RecordsOut:
    data_import = db.get(DataImport, import_id)
    if data_import is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("Import not found")
    return RecordsOut(counts=count_records(db, import_id))


# --- undo ------------------------------------------------


def undo_import(db: Session, tenant, import_id: uuid.UUID, meta: RequestMeta) -> UndoResultOut:
    try:
        return _undo(db, tenant, import_id, meta)
    except BaseException:
        db.rollback()
        raise


def _undo(db, tenant, import_id, meta) -> UndoResultOut:
    data_import = _locked(db, import_id)
    if data_import.status == "undone":
        raise ConflictError("This import has already been undone", code="already_undone")
    if data_import.status != "imported":
        raise ConflictError("Only an import that has been done can be undone", code="not_imported")

    refresh_statistics(db)  # so the deletes below are planned for the real table sizes
    removed = count_records(db, data_import.id)
    try:
        with db.begin_nested():
            for model in UNDO_ORDER:
                db.execute(
                    model.__table__.delete().where(
                        model.__table__.c.import_id == data_import.id,
                        model.__table__.c.organization_id == tenant.organization_id,
                    )
                )
    except IntegrityError as exc:
        raise ConflictError(
            "Some of this import's records are used by data added since (for example by a later "
            "import or something typed in by hand). Undo the later import first.",
            code="undo_blocked",
            details={"constraint": getattr(exc.orig.diag, "constraint_name", None)},
        ) from exc

    db.execute(
        update(DataImportRow)
        .where(DataImportRow.import_id == data_import.id, DataImportRow.status == "imported")
        .values(status="valid", target_table=None, target_id=None)
    )
    data_import.status = "undone"
    data_import.undone_at = datetime.now(UTC)
    record_audit(
        db,
        AuditAction.IMPORT_UNDONE,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_import",
        target_id=data_import.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"dataset": data_import.dataset, "removed": removed},
    )
    db.commit()
    db.refresh(data_import)
    return UndoResultOut(data_import=ImportOut.model_validate(data_import), removed=removed)
