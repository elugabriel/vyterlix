"""The diagnostic engine's tables (Phase 7): what changed, why, and the evidence.

- detection_events: something worth a look that the engine noticed in a business's figures, such
  as "sales fell 23% in September" (a material change). One row per KPI, kind and period, with
  the numbers behind it, how serious it is, whether it is good or bad news, and a plain-English
  summary. A later step adds unusual-month (anomaly) events to the same table.
- diagnoses: the engine's answer to "why did this happen?" for one event: a headline, a summary,
  and how confident it is.
- diagnostic_evidence: every statement behind a diagnosis, each marked with what kind of
  statement it is, so the screen never presents a guess as a fact:
    fact             a figure read straight from the business's records
    statistical      something worked out from the figures (a share, a trend, an outlier)
    ai_interpretation  a reading by the AI assistant, shown as such
    insufficient     the engine says it cannot tell, and what is missing
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

EVENT_KINDS = ("material_change", "anomaly")
EVENT_DIRECTIONS = ("up", "down")
EVENT_SEVERITIES = ("notable", "major")
EVENT_EFFECTS = ("good", "bad", "neutral")  # what the change means for the business
CHANGE_UNITS = ("percent", "points")  # points = percentage points, for figures that are already %
EVENT_STATUSES = ("open", "dismissed", "diagnosed")
DIAGNOSIS_STATUSES = ("ready", "insufficient_evidence")
CONFIDENCE_LABELS = ("high", "medium", "low", "insufficient")
EVIDENCE_TYPES = ("fact", "statistical", "ai_interpretation", "insufficient")


class DetectionEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "detection_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_detection_events_org_id"),
        UniqueConstraint(
            "organization_id",
            "kpi_id",
            "kind",
            "granularity",
            "period_start",
            name="uq_detection_events_kpi_period",
        ),
        CheckConstraint(_one_of("kind", EVENT_KINDS), name="kind_valid"),
        CheckConstraint("granularity = 'month'", name="granularity_valid"),
        CheckConstraint(_one_of("direction", EVENT_DIRECTIONS), name="direction_valid"),
        CheckConstraint(_one_of("severity", EVENT_SEVERITIES), name="severity_valid"),
        CheckConstraint(_one_of("effect", EVENT_EFFECTS), name="effect_valid"),
        CheckConstraint(_one_of("change_unit", CHANGE_UNITS), name="change_unit_valid"),
        CheckConstraint(_one_of("status", EVENT_STATUSES), name="status_valid"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        CheckConstraint(
            "data_quality IS NULL OR data_quality BETWEEN 0 AND 100", name="quality_in_range"
        ),
        CheckConstraint("length(trim(summary)) > 0", name="summary_not_blank"),
        Index("ix_detection_events_org_period", "organization_id", "period_start"),
        Index("ix_detection_events_kpi", "kpi_id"),
    )

    kpi_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kpi_definitions.id"))
    kind: Mapped[str] = mapped_column(String(20), server_default="material_change")
    granularity: Mapped[str] = mapped_column(String(10), server_default="month")
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(5))
    severity: Mapped[str] = mapped_column(String(10))
    effect: Mapped[str] = mapped_column(String(10))
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    reference_value: Mapped[Decimal] = mapped_column(Numeric(24, 6))  # what it is compared with
    change: Mapped[Decimal] = mapped_column(Numeric(14, 4))  # signed, in `change_unit`
    change_unit: Mapped[str] = mapped_column(String(10))
    # True when the business's own busy/quiet seasons lead you to expect about this change.
    explained_by_season: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    data_quality: Mapped[int | None] = mapped_column(SmallInteger)  # of the period, 0-100
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default="open")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Diagnosis(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "diagnoses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["detection_events.organization_id", "detection_events.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_diagnoses_org_id"),
        UniqueConstraint("event_id", name="uq_diagnoses_event"),
        CheckConstraint(_one_of("status", DIAGNOSIS_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("confidence_label", CONFIDENCE_LABELS), name="confidence_valid"),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 100", name="confidence_in_range"
        ),
        CheckConstraint(
            "(status = 'insufficient_evidence') = (confidence_label = 'insufficient')",
            name="insufficient_matches_label",
        ),
        CheckConstraint("length(trim(headline)) > 0", name="headline_not_blank"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(25))
    headline: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(SmallInteger)  # 0-100
    confidence_label: Mapped[str] = mapped_column(String(15))
    confidence_note: Mapped[str | None] = mapped_column(Text)  # how the confidence was reached
    # Which version of the rules produced this, and the figures it was based on, so any
    # diagnosis can be traced back to exactly what it was built from.
    rules_version: Mapped[str] = mapped_column(String(30), server_default="diagnosis-1")
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiagnosticEvidence(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "diagnostic_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "diagnosis_id"],
            ["diagnoses.organization_id", "diagnoses.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("evidence_type", EVIDENCE_TYPES), name="evidence_type_valid"),
        CheckConstraint("length(trim(statement)) > 0", name="statement_not_blank"),
        Index("ix_diagnostic_evidence_diagnosis", "diagnosis_id", "sort_order"),
    )

    diagnosis_id: Mapped[uuid.UUID] = mapped_column()
    evidence_type: Mapped[str] = mapped_column(String(20))
    statement: Mapped[str] = mapped_column(Text)  # one plain-English sentence
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
