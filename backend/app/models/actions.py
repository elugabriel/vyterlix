"""Action management (Phase 10): turning a recommendation into work that is tracked to the end.

- interventions: what the business decided to do, as accepted from a recommendation: the action as
  it was chosen (and as the owner reworded it, if they did), the figure it is meant to move, that
  figure's level when it was accepted, and what it was expected to win back. This is the record
  that later phases measure the outcome against (Phase 11). It keeps copies rather than links, so
  it stands even if the recommendation is later re-made.
- actions: the work itself: who does it, when it starts and is due, the steps, and where it has got
  to (pending, accepted, in progress, partly done, done, cancelled, or overdue).
- action_updates: everything that happens to an action, in order (accepted, reassigned, dates moved,
  notes, status changes), so its history can always be read back.
- action_evidence: what was attached to show the work: a note, a link, or a file.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of
from app.models.kpi import KPI_CATEGORIES

ACTION_STATUSES = (
    "pending",  # proposed by someone outside their remit, waiting for approval
    "accepted",
    "in_progress",
    "partially_completed",
    "completed",
    "cancelled",
    "overdue",  # set by the system when the target date passes
)
UPDATE_KINDS = (
    "created",
    "approved",
    "rejected",
    "status",
    "note",
    "assignment",
    "dates",
    "modification",
    "steps",
    "evidence",
    "overdue",
    "outcome",
)
EVIDENCE_KINDS = ("note", "link", "file")


class BusinessIntervention(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "interventions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_interventions_org_id"),
        CheckConstraint(_one_of("category", KPI_CATEGORIES), name="category_valid"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        Index("ix_interventions_org_accepted", "organization_id", "accepted_at"),
    )

    # Copies, not links: the recommendation may be re-made later, and the change it answered may go.
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column()
    event_id: Mapped[uuid.UUID | None] = mapped_column()
    library_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intervention_library.id"))
    library_code: Mapped[str | None] = mapped_column(String(60))
    kpi_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kpi_definitions.id"))
    category: Mapped[str] = mapped_column(String(15))
    title: Mapped[str] = mapped_column(String(200))
    original_title: Mapped[str | None] = mapped_column(String(200))  # set if the owner reworded it
    description: Mapped[str] = mapped_column(Text)
    target_label: Mapped[str | None] = mapped_column(String(200))
    expected_impact_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    expected_impact_unit: Mapped[str | None] = mapped_column(String(10))
    baseline_period: Mapped[date | None] = mapped_column(Date)  # the month the figure was read for
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))  # and what it was
    recommendation_score: Mapped[int | None] = mapped_column()
    rules_version: Mapped[str | None] = mapped_column(String(30))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BusinessAction(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "intervention_id"],
            ["interventions.organization_id", "interventions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_actions_org_id"),
        UniqueConstraint("intervention_id", name="uq_actions_intervention"),
        CheckConstraint(_one_of("status", ACTION_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("category", KPI_CATEGORIES), name="category_valid"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "start_date IS NULL OR target_date IS NULL OR target_date >= start_date",
            name="dates_in_order",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)", name="completed_has_time"
        ),
        Index("ix_actions_org_status", "organization_id", "status"),
        Index("ix_actions_org_owner", "organization_id", "owner_user_id"),
        Index("ix_actions_org_target", "organization_id", "target_date"),
    )

    intervention_id: Mapped[uuid.UUID] = mapped_column()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(15))  # the KPI area, for Manager remits
    # [{"text": "...", "done": false}, ...]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(25))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionUpdate(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "action_updates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["actions.organization_id", "actions.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("kind", UPDATE_KINDS), name="kind_valid"),
        Index("ix_action_updates_action", "action_id", "created_at"),
    )

    action_id: Mapped[uuid.UUID] = mapped_column()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))
    from_status: Mapped[str | None] = mapped_column(String(25))
    to_status: Mapped[str | None] = mapped_column(String(25))
    note: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionEvidence(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "action_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["actions.organization_id", "actions.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("kind", EVIDENCE_KINDS), name="kind_valid"),
        CheckConstraint("kind <> 'link' OR url IS NOT NULL", name="link_has_url"),
        CheckConstraint("kind <> 'file' OR storage_key IS NOT NULL", name="file_has_key"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        Index("ix_action_evidence_action", "action_id", "created_at"),
    )

    action_id: Mapped[uuid.UUID] = mapped_column()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500))
    storage_key: Mapped[str | None] = mapped_column(String(200))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
