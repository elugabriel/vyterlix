"""Business memory (Phase 12): what Vyterlix has learned about this business, kept so it can be
shown to the owner and used when ranking the next recommendation.

- business_memory: one fact per row. Some are worked out from the business's own data (what is
  normal for each figure, patterns in customers, goals, seasons); some are written by the owner
  (the limits on what they can do, and what they prefer).
- business_learning: one lesson per measured outcome, in words.
- intervention_patterns: how each kind of action has worked on each figure, counted up.
- memory_retrieval_events: every time memory was used to make a recommendation, what was used, so it
  can always be shown what the suggestion was based on.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
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

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

MEMORY_KINDS = ("normal_range", "customer_pattern", "goal", "season", "constraint", "preference")
MEMORY_SOURCES = ("derived", "owner")
LEARNING_OUTCOMES = ("successful", "partially_successful", "unsuccessful", "inconclusive")


class BusinessMemory(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "business_memory"
    __table_args__ = (
        UniqueConstraint("organization_id", "kind", "key", name="uq_business_memory_key"),
        CheckConstraint(_one_of("kind", MEMORY_KINDS), name="kind_valid"),
        CheckConstraint(_one_of("source", MEMORY_SOURCES), name="source_valid"),
        CheckConstraint("length(trim(key)) > 0", name="key_not_blank"),
    )

    kind: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)  # the fact in plain English
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    source: Mapped[str] = mapped_column(String(10))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class BusinessLearning(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "business_learning"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "intervention_id"],
            ["interventions.organization_id", "interventions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("intervention_id", name="uq_business_learning_intervention"),
        CheckConstraint(_one_of("outcome", LEARNING_OUTCOMES), name="outcome_valid"),
        Index("ix_business_learning_org_kpi", "organization_id", "kpi_code"),
    )

    intervention_id: Mapped[uuid.UUID] = mapped_column()
    library_code: Mapped[str | None] = mapped_column(String(60))
    kpi_code: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(25))
    achieved_pct: Mapped[int | None] = mapped_column(SmallInteger)
    lesson: Mapped[str] = mapped_column(Text)


class InterventionPattern(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "intervention_patterns"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "library_code", "kpi_code", name="uq_intervention_patterns_key"
        ),
        CheckConstraint(
            "successful >= 0 AND partially_successful >= 0 AND unsuccessful >= 0 "
            "AND inconclusive >= 0",
            name="counts_not_negative",
        ),
    )

    library_code: Mapped[str] = mapped_column(String(60))
    kpi_code: Mapped[str] = mapped_column(String(60))
    successful: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    partially_successful: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    unsuccessful: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    inconclusive: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    average_achieved_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    last_outcome: Mapped[str | None] = mapped_column(String(25))
    last_measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryRetrievalEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "memory_retrieval_events"
    __table_args__ = (Index("ix_memory_retrieval_org", "organization_id", "created_at"),)

    purpose: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[uuid.UUID | None] = mapped_column()  # the change it was used for
    used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
