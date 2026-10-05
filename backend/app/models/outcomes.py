"""Follow-up and outcome measurement (Phase 11): did the action do what it was meant to?

- follow_up_schedules: when a finished action is to be checked, which month's figure will be used,
  who is told, and whether that has happened yet.
- intervention_outcomes: the result of the check: the figure when the action was accepted, the
  figure afterwards, what was expected, what actually changed (also with the normal change for the
  time of year taken out, when last year's figures exist), the verdict, and the reason in words.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

FOLLOW_UP_STATUSES = ("scheduled", "done")
OUTCOMES = ("successful", "partially_successful", "unsuccessful", "inconclusive")


class FollowUpSchedule(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "follow_up_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "intervention_id"],
            ["interventions.organization_id", "interventions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("intervention_id", name="uq_follow_up_schedules_intervention"),
        CheckConstraint(_one_of("status", FOLLOW_UP_STATUSES), name="status_valid"),
        Index("ix_follow_up_schedules_due", "organization_id", "status", "due_date"),
    )

    intervention_id: Mapped[uuid.UUID] = mapped_column()
    action_id: Mapped[uuid.UUID | None] = mapped_column()
    due_date: Mapped[date] = mapped_column(Date)
    measure_month: Mapped[date] = mapped_column(Date)  # the month whose figure is compared
    status: Mapped[str] = mapped_column(String(10), server_default="scheduled")
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterventionOutcome(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "intervention_outcomes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "intervention_id"],
            ["interventions.organization_id", "interventions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("intervention_id", name="uq_intervention_outcomes_intervention"),
        CheckConstraint(_one_of("outcome", OUTCOMES), name="outcome_valid"),
        CheckConstraint(
            "data_quality IS NULL OR data_quality BETWEEN 0 AND 100", name="quality_in_range"
        ),
        Index("ix_intervention_outcomes_org", "organization_id", "measured_at"),
    )

    intervention_id: Mapped[uuid.UUID] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(25))
    reason: Mapped[str] = mapped_column(Text)
    baseline_period: Mapped[date | None] = mapped_column(Date)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    measured_period: Mapped[date | None] = mapped_column(Date)
    measured_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    expected_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))  # in the good direction
    actual_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))  # in the good direction
    seasonal_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    adjusted_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    achieved_pct: Mapped[int | None] = mapped_column(SmallInteger)
    data_quality: Mapped[int | None] = mapped_column(SmallInteger)
    rules_version: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    measured_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )  # nobody when the scheduler did it
