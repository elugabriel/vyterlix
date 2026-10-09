"""Reports (Phase 15): the business's figures, written up to be read, printed or sent on.

- reports: a saved report: which kind it is and how many months it covers.
- report_runs: one copy of a report written for one person. The whole content is stored as it was
  when it was written (so a report never changes afterwards and can always be checked), and the PDF
  and CSV are made from that copy. A copy is for the person it was written for, because what it
  contains depends on their role and area.
- report_schedules: a report that is written and emailed again and again (weekly or monthly) to the
  people chosen.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

REPORT_KINDS = ("monthly", "health", "kpi", "outcomes")
FREQUENCIES = ("weekly", "monthly")
TRIGGERS = ("manual", "scheduled")


class Report(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_reports_org_id"),
        UniqueConstraint("organization_id", "kind", name="uq_reports_kind"),
        CheckConstraint(_one_of("kind", REPORT_KINDS), name="kind_valid"),
        CheckConstraint("months BETWEEN 1 AND 24", name="months_range"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    kind: Mapped[str] = mapped_column(String(15))
    name: Mapped[str] = mapped_column(String(100))
    months: Mapped[int] = mapped_column(SmallInteger, server_default=text("6"))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ReportSchedule(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "report_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "report_id"],
            ["reports.organization_id", "reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_report_schedules_org_id"),
        CheckConstraint(_one_of("frequency", FREQUENCIES), name="frequency_valid"),
        CheckConstraint(
            "(frequency = 'weekly' AND weekday IS NOT NULL AND weekday BETWEEN 1 AND 7 "
            "AND day_of_month IS NULL) "
            "OR (frequency = 'monthly' AND day_of_month IS NOT NULL "
            "AND day_of_month BETWEEN 1 AND 28 AND weekday IS NULL)",
            name="when_valid",
        ),
        Index("ix_report_schedules_due", "enabled", "next_run_at"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column()
    frequency: Mapped[str] = mapped_column(String(10))
    weekday: Mapped[int | None] = mapped_column(SmallInteger)  # 1 = Monday
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)  # 1 to 28
    recipients: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))  # user ids
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ReportRun(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "report_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "report_id"],
            ["reports.organization_id", "reports.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("trigger", TRIGGERS), name="trigger_valid"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        Index("ix_report_runs_user", "organization_id", "user_id", "generated_at"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_schedules.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[date] = mapped_column(Date)  # first day of the first month covered
    period_end: Mapped[date] = mapped_column(Date)  # last day of the last month covered
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the report as it was written
    rules_version: Mapped[str] = mapped_column(String(30))
    generated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )  # nobody when the schedule wrote it
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
