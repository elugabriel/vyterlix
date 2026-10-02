"""The KPI engine's tables (Phase 5).

- kpi_definitions: WHAT each KPI is, as data, not code. A definition names a plain-English
  meaning, a unit and an `expression` over named measures ("revenue - cogs"). The measures
  themselves (revenue, cogs, sales_count...) are small SQL aggregates in app/kpi/measures.py;
  a new KPI that only recombines existing measures is a new row, not a code change. Global
  (not tenant data), like `industries`; `industry_code` limits a KPI to one industry.
- kpi_calculation_runs: one run of the engine for a business (who asked, which periods, how it
  went), so a stale or failed calculation is visible.
- kpi_values: the answer for one KPI, one business, one period (a month, a week...), with the
  previous period's value and the change, whether the period is finished, how good the data
  behind it was, and the measure values it was worked out from (so any number can be explained).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.permissions import KpiCategory
from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

KPI_CATEGORIES = tuple(c.value for c in KpiCategory)
KPI_UNITS = ("gbp", "percent", "count", "ratio")
KPI_DIRECTIONS = ("up_good", "down_good", "neutral")  # which way is better, for colouring
GRANULARITIES = ("week", "month", "quarter", "year")
VALUE_STATUSES = (
    "ok",
    "no_data",  # the business has no records of a kind this KPI needs
    "undefined",  # the sum is fine but the KPI can't be worked out (e.g. margin with no sales)
)
RUN_STATUSES = ("running", "succeeded", "failed")
RUN_TRIGGERS = ("manual", "import")


class KpiDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "kpi_definitions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_kpi_definitions_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint(_one_of("category", KPI_CATEGORIES), name="category_valid"),
        CheckConstraint(_one_of("unit", KPI_UNITS), name="unit_valid"),
        CheckConstraint(_one_of("direction", KPI_DIRECTIONS), name="direction_valid"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(trim(expression)) > 0", name="expression_not_blank"),
        Index("ix_kpi_definitions_category", "category", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(400))  # plain English: what it tells you
    category: Mapped[str] = mapped_column(String(15))
    unit: Mapped[str] = mapped_column(String(10))
    # Arithmetic over measure names, e.g. "(revenue - cogs) / revenue * 100". Also
    # prev(measure) = the previous period, yoy(measure) = the same period a year earlier.
    expression: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(10), server_default="neutral")
    # Kinds of records the business must have for this KPI to mean anything.
    requires: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    industry_code: Mapped[str | None] = mapped_column(String(50), ForeignKey("industries.code"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class KpiCalculationRun(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "kpi_calculation_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_kpi_calculation_runs_org_id"),
        CheckConstraint(_one_of("status", RUN_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("trigger", RUN_TRIGGERS), name="trigger_valid"),
        CheckConstraint(_one_of("granularity", GRANULARITIES), name="granularity_valid"),
        CheckConstraint("period_to >= period_from", name="period_in_order"),
        CheckConstraint("status = 'running' OR finished_at IS NOT NULL", name="finished_has_time"),
        Index("ix_kpi_calculation_runs_org_started", "organization_id", "started_at"),
    )

    status: Mapped[str] = mapped_column(String(10), server_default="running")
    trigger: Mapped[str] = mapped_column(String(10), server_default="manual")
    granularity: Mapped[str] = mapped_column(String(10))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    kpi_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    values_written: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(500))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column()


class KpiValue(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "kpi_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["kpi_calculation_runs.organization_id", "kpi_calculation_runs.id"],
        ),
        UniqueConstraint(
            "organization_id", "kpi_id", "granularity", "period_start", name="uq_kpi_values_period"
        ),
        CheckConstraint(_one_of("granularity", GRANULARITIES), name="granularity_valid"),
        CheckConstraint(_one_of("status", VALUE_STATUSES), name="status_valid"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        CheckConstraint("status = 'ok' OR value IS NULL", name="only_ok_has_value"),
        CheckConstraint(
            "data_quality IS NULL OR data_quality BETWEEN 0 AND 100", name="quality_in_range"
        ),
        Index("ix_kpi_values_org_period", "organization_id", "granularity", "period_start"),
        Index("ix_kpi_values_kpi", "kpi_id"),
    )

    kpi_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kpi_definitions.id"))
    run_id: Mapped[uuid.UUID] = mapped_column()
    granularity: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)  # the last day of the period, inclusive
    is_complete: Mapped[bool] = mapped_column(Boolean)  # False while the period is still running
    status: Mapped[str] = mapped_column(String(10), server_default="ok")
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    data_quality: Mapped[int | None] = mapped_column(SmallInteger)  # 0-100, from Phase 4 step 8
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
