"""Import tracking (Phase 4): where uploaded files and connected sources came from, what
happened to every row, and what's missing or doubtful in a business's data.

- data_sources: a named source a business imports from repeatedly ("Till export",
  "Xero"), remembering its column mapping so the next upload maps itself.
- data_imports: one upload (or, later, one sync): the file, its fingerprint, the chosen
  mapping and options, row counts, and its status through upload -> import -> undo.
- data_import_rows: every row of the file, raw as uploaded, with its problems and the
  record it became. Lets the user download "rows with problems" and lets undo find
  exactly what an import created (trading rows also carry import_id).
- data_quality_issues: gaps and doubts found in the data (a missing month, sales with
  no cost), shown to the user and fed into the data-quality score.

Original files are kept for 90 days (upload_retention_days) and then deleted by
`python -m app.cli.uploads purge`; the import record and its rows stay.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

# What an import fills. Each maps to one trading table (see app/models/data.py).
DATASETS = ("sales", "expenses", "customers", "suppliers", "products", "stock_movements")
FILE_SOURCES = ("csv", "excel")  # .csv and .xlsx uploads
SOURCE_KINDS = ("csv", "excel", "xero", "shopify", "woocommerce", "google_analytics")
FILE_SUFFIXES = {"csv": ".csv", "excel": ".xlsx"}
IMPORT_STATUSES = (
    "uploaded",  # file stored and read; waiting for column mapping
    "mapped",  # columns matched to fields
    "validated",  # every row checked; preview ready
    "importing",
    "imported",
    "failed",
    "undone",
)
ROW_STATUSES = ("pending", "valid", "invalid", "duplicate", "imported", "skipped")
ISSUE_SEVERITIES = ("info", "warning", "critical")


def _tenant_fk(column: str, target: str, *, ondelete: str | None = None) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete=ondelete,
    )


class DataSource(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_data_sources_org_id"),
        UniqueConstraint("organization_id", "name", name="uq_data_sources_name"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(_one_of("kind", SOURCE_KINDS), name="kind_valid"),
        CheckConstraint(_one_of("dataset", DATASETS), name="dataset_valid"),
    )

    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    dataset: Mapped[str] = mapped_column(String(20))
    # {"field": "Column heading in the file"} remembered from the last import.
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataImport(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "data_imports"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_data_imports_org_id"),
        _tenant_fk("data_source_id", "data_sources"),
        CheckConstraint(_one_of("source", FILE_SOURCES), name="source_valid"),
        CheckConstraint(_one_of("dataset", DATASETS), name="dataset_valid"),
        CheckConstraint(_one_of("status", IMPORT_STATUSES), name="status_valid"),
        CheckConstraint("length(trim(original_filename)) > 0", name="filename_not_blank"),
        CheckConstraint("file_size_bytes >= 0", name="file_size_not_negative"),
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="file_sha256_format"),
        CheckConstraint("source = 'excel' OR sheet_name IS NULL", name="sheet_only_for_excel"),
        CheckConstraint("header_row >= 1", name="header_row_positive"),
        CheckConstraint(
            "row_count >= 0 AND valid_count >= 0 AND invalid_count >= 0"
            " AND duplicate_count >= 0 AND imported_count >= 0",
            name="counts_not_negative",
        ),
        CheckConstraint(
            "valid_count + invalid_count + duplicate_count <= row_count", name="counts_add_up"
        ),
        CheckConstraint(
            "status NOT IN ('imported', 'undone') OR imported_at IS NOT NULL",
            name="imported_has_time",
        ),
        CheckConstraint("status <> 'undone' OR undone_at IS NOT NULL", name="undone_has_time"),
        # "You uploaded this exact file on 3 May": warn before importing it twice.
        Index("ix_data_imports_org_sha256", "organization_id", "file_sha256"),
        Index("ix_data_imports_org_created", "organization_id", "created_at"),
    )

    data_source_id: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(20))
    dataset: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="uploaded")

    original_filename: Mapped[str] = mapped_column(String(255))  # shown to the user only
    storage_key: Mapped[str] = mapped_column(String(200))  # generated by us, never user input
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    file_sha256: Mapped[str] = mapped_column(String(64))
    sheet_name: Mapped[str | None] = mapped_column(String(100))
    header_row: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    # e.g. {"vat_inclusive": true}: asked per import (decision 2026-09-28).
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))

    row_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    valid_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    invalid_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    imported_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(String(500))  # why it failed

    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataImportRow(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "data_import_rows"
    __table_args__ = (
        _tenant_fk("import_id", "data_imports", ondelete="CASCADE"),
        UniqueConstraint("import_id", "row_number", name="uq_data_import_rows_row"),
        CheckConstraint("row_number >= 1", name="row_number_positive"),
        CheckConstraint(_one_of("status", ROW_STATUSES), name="status_valid"),
        CheckConstraint(
            "(target_table IS NULL) = (target_id IS NULL)", name="target_table_and_id_together"
        ),
        CheckConstraint(
            "status <> 'imported' OR target_id IS NOT NULL", name="imported_has_target"
        ),
        Index("ix_data_import_rows_import_status", "import_id", "status"),
    )

    import_id: Mapped[uuid.UUID] = mapped_column()
    row_number: Mapped[int] = mapped_column(Integer)  # as the user sees it in their file
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"heading": "cell text"}
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    # [{"field": "sold_on", "code": "invalid_date", "message": "..."}]
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    target_table: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[uuid.UUID | None] = mapped_column()


class DataQualityIssue(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        _tenant_fk("import_id", "data_imports", ondelete="CASCADE"),
        CheckConstraint(_one_of("dataset", DATASETS), name="dataset_valid"),
        CheckConstraint(_one_of("severity", ISSUE_SEVERITIES), name="severity_valid"),
        CheckConstraint("issue_type ~ '^[a-z][a-z_]*$'", name="issue_type_format"),
        CheckConstraint("affected_count >= 0", name="affected_count_not_negative"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        Index("ix_data_quality_issues_org_open", "organization_id", "resolved_at"),
    )

    import_id: Mapped[uuid.UUID | None] = mapped_column()  # NULL = about the data as a whole
    dataset: Mapped[str] = mapped_column(String(20))
    issue_type: Mapped[str] = mapped_column(String(40))  # e.g. missing_month, missing_cost
    severity: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    affected_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    message: Mapped[str] = mapped_column(String(500))  # plain English, UK dates
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
