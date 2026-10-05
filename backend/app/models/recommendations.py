"""The recommendation engine's tables (Phase 9): what to do about a change, and why.

- intervention_library: the actions Vyterlix knows how to suggest, as data (a plain description,
  the steps, which findings it answers, how much effort and cost, how long it takes to show, and a
  starting estimate of how much of a problem it usually recovers). Global, like kpi_definitions.
  The estimates are Vyterlix's own starting values, labelled as such, to be replaced by what really
  happened once outcomes are tracked (Phase 11).
- recommendations: one per detected change that has been explained: a headline, the rationale for
  the option chosen, and which rules made it. Re-making it replaces the options.
- recommendation_options: every action considered for that change, each scored on the same seven
  things (impact, confidence, fit with the owner's goals, urgency, ease, track record), with the
  working kept so the score can be explained. Exactly one is marked as the recommended one.
- recommendation_evidence: the statements behind a recommendation, typed like diagnosis evidence
  (fact / statistical / ai_interpretation / insufficient), so a guess is never shown as a fact.

Ranking is done by rules and arithmetic, never by a language model.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from app.models.diagnostics import EVIDENCE_TYPES
from app.models.identity import _one_of
from app.models.kpi import KPI_CATEGORIES

LEVELS = ("low", "medium", "high")
COST_LEVELS = ("none", "low", "medium", "high")
RECOMMENDATION_STATUSES = (
    "open",
    "no_action_needed",
    "insufficient_evidence",
    "dismissed",  # the owner decided not to act
    "proposed",  # someone outside their remit put it forward; waiting for approval
    "accepted",  # being done: see the action
)


class Intervention(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "intervention_library"
    __table_args__ = (
        UniqueConstraint("code", name="uq_intervention_library_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint(_one_of("category", KPI_CATEGORIES), name="category_valid"),
        CheckConstraint(_one_of("effort", LEVELS), name="effort_valid"),
        CheckConstraint(_one_of("cost_level", COST_LEVELS), name="cost_valid"),
        CheckConstraint("impact_share BETWEEN 0 AND 1", name="impact_share_range"),
        CheckConstraint("typical_days_to_effect >= 0", name="days_not_negative"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(15))
    summary: Mapped[str] = mapped_column(String(400))  # plain English: what it is and why it works
    steps: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    # Which findings it answers: [{"kind": "contributor", "dimension": "Product"}, ...]. A finding
    # of that kind (and, for a part, that way of splitting) that hurts the figure calls it up.
    addresses: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    kpis: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))  # figures it helps
    goal_types: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    effort: Mapped[str] = mapped_column(String(10))
    cost_level: Mapped[str] = mapped_column(String(10))
    typical_days_to_effect: Mapped[int] = mapped_column(SmallInteger)
    # The share (0 to 1) of the gap it is expected to recover. A starting estimate, not a promise.
    impact_share: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    impact_basis: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(20), server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Recommendation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["detection_events.organization_id", "detection_events.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "diagnosis_id"],
            ["diagnoses.organization_id", "diagnoses.id"],
            ondelete="SET NULL",
        ),
        UniqueConstraint("organization_id", "id", name="uq_recommendations_org_id"),
        UniqueConstraint("event_id", name="uq_recommendations_event"),
        CheckConstraint(_one_of("status", RECOMMENDATION_STATUSES), name="status_valid"),
        CheckConstraint("length(trim(headline)) > 0", name="headline_not_blank"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column()
    diagnosis_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(25), server_default="open")
    headline: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str] = mapped_column(Text)  # why this one
    rules_version: Mapped[str] = mapped_column(String(30))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecommendationOption(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "recommendation_options"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "recommendation_id"],
            ["recommendations.organization_id", "recommendations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_recommendation_options_org_id"),
        UniqueConstraint("recommendation_id", "rank", name="uq_recommendation_options_rank"),
        CheckConstraint(_one_of("effort", LEVELS), name="effort_valid"),
        CheckConstraint(_one_of("cost_level", COST_LEVELS), name="cost_valid"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint(
            "confidence BETWEEN 0 AND 100 AND goal_fit BETWEEN 0 AND 100"
            " AND urgency BETWEEN 0 AND 100 AND ease BETWEEN 0 AND 100"
            " AND impact_score BETWEEN 0 AND 100"
            " AND history_score BETWEEN 0 AND 100 AND total_score BETWEEN 0 AND 100",
            name="scores_in_range",
        ),
        # Exactly one option per recommendation is the recommended one, and it is the top ranked.
        Index(
            "uq_recommendation_options_recommended",
            "recommendation_id",
            unique=True,
            postgresql_where=text("is_recommended"),
        ),
        CheckConstraint("NOT is_recommended OR rank = 1", name="recommended_is_first"),
        Index("ix_recommendation_options_recommendation", "recommendation_id", "rank"),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column()
    intervention_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intervention_library.id"))
    rank: Mapped[int] = mapped_column(SmallInteger)
    is_recommended: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    target_label: Mapped[str | None] = mapped_column(String(200))  # e.g. the product it is about
    impact_value: Mapped[Decimal] = mapped_column(Numeric(24, 6))  # in the figure's own unit
    impact_unit: Mapped[str] = mapped_column(String(10))
    impact_score: Mapped[int] = mapped_column(SmallInteger)
    confidence: Mapped[int] = mapped_column(SmallInteger)
    goal_fit: Mapped[int] = mapped_column(SmallInteger)
    urgency: Mapped[int] = mapped_column(SmallInteger)
    ease: Mapped[int] = mapped_column(SmallInteger)
    history_score: Mapped[int] = mapped_column(SmallInteger)
    total_score: Mapped[int] = mapped_column(SmallInteger)
    effort: Mapped[str] = mapped_column(String(10))
    cost_level: Mapped[str] = mapped_column(String(10))
    days_to_effect: Mapped[int] = mapped_column(SmallInteger)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class RecommendationEvidence(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "recommendation_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "recommendation_id"],
            ["recommendations.organization_id", "recommendations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("evidence_type", EVIDENCE_TYPES), name="evidence_type_valid"),
        CheckConstraint("length(trim(statement)) > 0", name="statement_not_blank"),
        Index("ix_recommendation_evidence_recommendation", "recommendation_id", "sort_order"),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column()
    evidence_type: Mapped[str] = mapped_column(String(20))
    statement: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
