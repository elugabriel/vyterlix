"""Checking every row of an upload before anything is imported (Phase 4 step 5).

The stored file is read row by row and each row is judged by `parse_row` using the saved
column mapping. Every row gets one of three outcomes, saved in `data_import_rows`:

- valid: will be imported.
- invalid: something in it can't be used (bad date, unreadable amount, ...). Its problems are
  listed so the person can fix them and re-upload.
- duplicate: the same record is already in their data, or appears earlier in this file.

Nothing is created in the person's data here. The session must be scoped to the organisation.
"""

import csv
import hashlib
import io
import re
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.models.data import Customer, Expense, Product, Sale, StockMovement, Supplier
from app.models.imports import DataImport, DataImportRow
from app.schemas.import_validation import RowOut, RowsOut, ValidationOut
from app.services import import_fields as fields
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.file_reader import open_rows
from app.services.import_rows import RowOutcome, parse_row
from app.services.imports import clear_validation, get_record
from app.services.storage import FileStorage

BATCH = 2000
EXAMPLE_ROWS = 5
MAX_PROBLEM_KINDS = 30
_FORMULA_START = re.compile(r"^[=@\t\r]|^[+-](?![\d.,]*$)")
_REFERENCE_MODELS = {
    "sales": Sale,
    "expenses": Expense,
    "stock_movements": StockMovement,
    "customers": Customer,
}
_DATE_FIELDS = {"sales": "sold_on", "expenses": "spent_on"}
# Rows recognised by name or email have no ID that could disagree: a second "Flour Co" is just
# the same supplier again. Rows recognised by a reference or code that differ in their details
# are a conflict the person must settle.
_SAME_THING_KEYS = ("name", "email")


# --- spotting repeats ----------------------------------------


def existing_keys(db: Session, dataset: str, source: str, keys: set[tuple[str, str]]) -> set:
    """Which of these keys already exist in the business's data."""
    found: set[tuple[str, str]] = set()
    by_kind: dict[str, set[str]] = defaultdict(set)
    for kind, value in keys:
        by_kind[kind].add(value)

    if refs := by_kind.get("ref"):
        model = _REFERENCE_MODELS[dataset]
        rows = db.scalars(
            select(model.source_ref).where(model.source == source, model.source_ref.in_(refs))
        )
        found |= {("ref", r) for r in rows}
    if emails := by_kind.get("email"):
        found |= {
            ("email", e)
            for e in db.scalars(select(Customer.email).where(Customer.email.in_(emails)))
        }
    if skus := by_kind.get("sku"):
        found |= {("sku", s) for s in db.scalars(select(Product.sku).where(Product.sku.in_(skus)))}
    if names := by_kind.get("name"):
        model = Supplier if dataset == "suppliers" else Product
        lowered = func.lower(model.name)
        found |= {("name", n) for n in db.scalars(select(lowered).where(lowered.in_(names)))}
    return found


def _fingerprint(values: dict[str, Any]) -> bytes:
    text = "|".join(f"{k}={values[k]}" for k in sorted(values))
    return hashlib.blake2b(text.encode(), digest_size=16).digest()


# --- the run ----------------------------------------


class _Tally:
    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        self.counts = {"valid": 0, "invalid": 0, "duplicate": 0}
        self.problems: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.totals = {
            "net_amount": Decimal(0),
            "vat_amount": Decimal(0),
            "gross_amount": Decimal(0),
        }
        self.dates: list[date] = []

    def problem(self, row_number: int, error: dict[str, str]) -> None:
        entry = self.problems.setdefault(
            (error["code"], error["field"]),
            {
                "code": error["code"],
                "field": error["field"],
                "count": 0,
                "rows": [],
                "example": f"Row {row_number}: {error['message']}",
            },
        )
        entry["count"] += 1
        if len(entry["rows"]) < EXAMPLE_ROWS:
            entry["rows"].append(row_number)

    def accept(self, outcome: RowOutcome) -> None:
        if self.dataset in _DATE_FIELDS:
            for key in self.totals:
                self.totals[key] += outcome.values[key]
            self.dates.append(outcome.values[_DATE_FIELDS[self.dataset]])


def _classify(
    outcomes: list[tuple[int, dict[str, str], RowOutcome]],
    existing: set,
    first_seen: dict[tuple[str, str], tuple[int, bytes]],
    tally: _Tally,
) -> list[dict[str, Any]]:
    saved = []
    for row_number, raw, outcome in outcomes:
        status, errors = "valid", outcome.errors
        if errors:
            status = "invalid"
        elif outcome.key is not None:
            earlier = first_seen.get(outcome.key)
            if earlier is not None:
                first_row, digest = earlier
                same = digest == _fingerprint(outcome.values)
                if same or outcome.key[0] in _SAME_THING_KEYS:
                    status = "duplicate"
                    errors = [_error("duplicate_in_file", f"Same as row {first_row}.")]
                else:
                    status = "invalid"
                    errors = [
                        _error(
                            "repeated_reference",
                            f"'{outcome.key[1]}' is also on row {first_row} with different "
                            "details. Each order, invoice or product code should be on one row.",
                            "reference",
                        )
                    ]
            elif outcome.key in existing:
                status = "duplicate"
                errors = [_error("already_imported", "This is already in your data.")]
            else:
                first_seen[outcome.key] = (row_number, _fingerprint(outcome.values))
        tally.counts[status] += 1
        if status == "valid":
            tally.accept(outcome)
        for error in errors:
            tally.problem(row_number, error)
        saved.append({"row_number": row_number, "raw": raw, "status": status, "errors": errors})
    return saved


def _error(code: str, message: str, field: str | None = "reference") -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _warnings(data_import: DataImport, tally: _Tally, total: int) -> list[dict[str, str]]:
    mapping, dataset, warnings = data_import.column_mapping, data_import.dataset, []
    if tally.counts["valid"] == 0:
        warnings.append(
            {
                "code": "nothing_to_import",
                "message": "No row can be imported. Check the column mapping or fix the file.",
            }
        )
    elif tally.counts["invalid"] > total / 5:
        warnings.append(
            {
                "code": "many_problems",
                "message": "More than a fifth of the rows have problems. Check that the "
                "columns are mapped correctly before importing.",
            }
        )
    if dataset in ("sales", "expenses", "stock_movements") and "reference" not in mapping:
        warnings.append(
            {
                "code": "no_reference",
                "message": "No order, invoice or reference column is mapped, so uploading "
                "an overlapping file later can't be checked for repeats.",
            }
        )
    if dataset == "sales" and "cost" not in mapping:
        warnings.append(
            {
                "code": "no_cost_of_goods",
                "message": "There is no cost of goods column, so profit margins can't be "
                "worked out from these sales.",
            }
        )
    return warnings


def _validate(
    db: Session,
    storage: FileStorage,
    tenant,
    import_id: uuid.UUID,
    meta: RequestMeta,
    *,
    today: date | None = None,
) -> ValidationOut:
    data_import = get_record(db, import_id)
    if data_import.status == "uploaded":
        raise ConflictError("Map the columns before checking the data", code="mapping_needed")
    if data_import.status not in ("mapped", "validated"):
        raise ConflictError(
            "This import has already run, so it can't be checked again", code="not_editable"
        )
    if data_import.file_deleted_at is not None or not storage.exists(data_import.storage_key):
        raise ConflictError("The original file has been deleted", code="file_deleted")

    dataset, mapping, options = data_import.dataset, data_import.column_mapping, data_import.options
    tally, first_seen = _Tally(dataset), {}
    clear_validation(db, data_import)
    total = 0
    mapped_headers = list(dict.fromkeys(mapping.values()))

    with (
        storage.open(data_import.storage_key) as stream,
        open_rows(
            stream,
            data_import.source,
            sheet_name=data_import.sheet_name,
            header_row=data_import.header_row,
            max_rows=get_settings().max_upload_rows,
        ) as source,
    ):
        if fields.check_mapping(dataset, mapping, options, source.headers):
            raise ConflictError(
                "The column mapping no longer fits this file. Map the columns again.",
                code="mapping_incomplete",
            )
        position = {h: i for i, h in enumerate(source.headers)}
        batch: list[tuple[int, dict[str, str], RowOutcome]] = []

        def flush() -> None:
            nonlocal total
            if not batch:
                return
            keys = {o.key for _, _, o in batch if o.key is not None and o.ok}
            existing = existing_keys(db, dataset, data_import.source, keys) if keys else set()
            saved = _classify(batch, existing, first_seen, tally)
            db.execute(
                insert(DataImportRow),
                [
                    {"organization_id": tenant.organization_id, "import_id": data_import.id, **row}
                    for row in saved
                ],
            )
            total += len(batch)
            batch.clear()

        for row_number, cells in source.rows:
            raw = {h: cells[position[h]] for h in mapped_headers}
            batch.append((row_number, raw, parse_row(dataset, raw, mapping, options, today=today)))
            if len(batch) >= BATCH:
                flush()
        flush()

    if total == 0:
        raise AppError("There are no data rows to check.", code="no_data_rows", status_code=422)

    summary = _summary(data_import, tally, total)
    data_import.row_count = total
    data_import.valid_count = tally.counts["valid"]
    data_import.invalid_count = tally.counts["invalid"]
    data_import.duplicate_count = tally.counts["duplicate"]
    data_import.status = "validated"
    data_import.validation_summary = summary
    record_audit(
        db,
        AuditAction.IMPORT_VALIDATED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="data_import",
        target_id=data_import.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"rows": total, **tally.counts},
    )
    db.commit()
    return ValidationOut.model_validate(summary)


def validate_import(
    db: Session,
    storage: FileStorage,
    tenant,
    import_id: uuid.UUID,
    meta: RequestMeta,
    *,
    today: date | None = None,
) -> ValidationOut:
    """Check every row. All or nothing: if anything goes wrong, the earlier results stay."""
    try:
        return _validate(db, storage, tenant, import_id, meta, today=today)
    except BaseException:
        db.rollback()
        raise


def _summary(data_import: DataImport, tally: _Tally, total: int) -> dict[str, Any]:
    problems = sorted(tally.problems.values(), key=lambda p: -p["count"])[:MAX_PROBLEM_KINDS]
    has_money = data_import.dataset in _DATE_FIELDS
    return {
        "validated_at": datetime.now(UTC).isoformat(),
        "rows": total,
        "valid": tally.counts["valid"],
        "invalid": tally.counts["invalid"],
        "duplicate": tally.counts["duplicate"],
        "problems": problems,
        "totals": {
            "net": str(tally.totals["net_amount"].quantize(Decimal("0.01"))),
            "vat": str(tally.totals["vat_amount"].quantize(Decimal("0.01"))),
            "gross": str(tally.totals["gross_amount"].quantize(Decimal("0.01"))),
        }
        if has_money
        else None,
        "date_from": min(tally.dates).isoformat() if tally.dates else None,
        "date_to": max(tally.dates).isoformat() if tally.dates else None,
        "warnings": _warnings(data_import, tally, total),
        "can_import": tally.counts["valid"] > 0,
    }


# --- reading results back ----------------------------------------


def get_validation(db: Session, import_id: uuid.UUID) -> ValidationOut:
    data_import = get_record(db, import_id)
    if data_import.validation_summary is None:
        raise ConflictError("This import hasn't been checked yet", code="not_validated")
    return ValidationOut.model_validate(data_import.validation_summary)


def list_rows(
    db: Session, import_id: uuid.UUID, *, status: str | None, limit: int, offset: int
) -> RowsOut:
    data_import = get_record(db, import_id)
    if data_import.validation_summary is None:
        raise ConflictError("This import hasn't been checked yet", code="not_validated")
    query = select(DataImportRow).where(DataImportRow.import_id == data_import.id)
    if status:
        query = query.where(DataImportRow.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(DataImportRow.row_number).limit(limit).offset(offset))
    return RowsOut(
        total=total,
        rows=[
            RowOut(row_number=r.row_number, status=r.status, errors=r.errors, raw=r.raw)
            for r in rows
        ],
    )


def _safe_cell(value: str) -> str:
    """Stop a spreadsheet treating a cell as a formula (CSV injection)."""
    return f"'{value}" if _FORMULA_START.match(value) else value


def problems_csv(db: Session, import_id: uuid.UUID) -> tuple[str, str]:
    """(filename, CSV text) of the rows that were skipped, with what is wrong with each.
    Opens in Excel; fix the cells and upload it as a new file."""
    data_import = get_record(db, import_id)
    if data_import.validation_summary is None:
        raise ConflictError("This import hasn't been checked yet", code="not_validated")
    mapping = data_import.column_mapping
    # Columns in the order the fields are listed on the mapping screen.
    headers = list(
        dict.fromkeys(
            mapping[f.key] for f in fields.DATASET_FIELDS[data_import.dataset] if f.key in mapping
        )
    )
    out = io.StringIO()
    out.write("﻿")  # so Excel reads £ and accents correctly
    writer = csv.writer(out)
    writer.writerow(["Row", "Status", "Problems", *map(_safe_cell, headers)])
    rows = db.scalars(
        select(DataImportRow)
        .where(
            DataImportRow.import_id == data_import.id,
            DataImportRow.status.in_(("invalid", "duplicate")),
        )
        .order_by(DataImportRow.row_number)
    )
    for row in rows:
        problems = "; ".join(e["message"] for e in row.errors)
        writer.writerow(
            [
                row.row_number,
                row.status,
                _safe_cell(problems),
                *(_safe_cell(row.raw.get(h, "")) for h in headers),
            ]
        )
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", data_import.original_filename.rsplit(".", 1)[0])
    return f"problems - {stem}.csv", out.getvalue()
