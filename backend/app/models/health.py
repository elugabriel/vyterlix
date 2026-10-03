"""Business health (Phase 6): one score per business per period, built from the KPIs.

- health_rules: HOW each KPI is judged, as data (not compiled in). A rule says which KPI, in
  which area (category), how heavily it counts, whether higher or lower is better, and three
  anchors (bad / ok / good) the score is drawn between. `basis` says what is judged: the KPI's
  value itself (a margin of 12%) or how it compares with this business's own normal (sales 8%
  below their usual). A rule with an industry applies only to that industry and replaces the
  general rule for the same KPI.
- health_category_weights: how much each area counts towards the overall score (per industry
  if wanted), so a shop and a consultancy can weigh stock and customers differently.
- business_health: one row per business and period: the overall score, its status, how much of
  the picture could be scored (coverage), the trend, and a plain-English explanation.
- business_health_components: one row per area per period, with its score, trend, how many
  points it contributed, an explanation, and the metrics behind it (details), so every number
  can be clicked through to its evidence.
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

from app.core.permissions import KpiCategory
from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

HEALTH_CATEGORIES = tuple(c.value for c in KpiCategory)
RULE_BASES = ("value", "vs_baseline")
RULE_DIRECTIONS = ("up_good", "down_good")
HEALTH_STATUSES = ("healthy", "fair", "needs_attention", "at_risk")
# A score that can't be given, and why.
NO_SCORE_STATUSES = ("not_enough_data",)
TRENDS = ("up", "down", "flat")


class HealthRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "health_rules"
    __table_args__ = (
        CheckConstraint(_one_of("category", HEALTH_CATEGORIES), name="category_valid"),
        CheckConstraint(_one_of("basis", RULE_BASES), name="basis_valid"),
        CheckConstraint(_one_of("direction", RULE_DIRECTIONS), name="direction_valid"),
        CheckConstraint("weight > 0", name="weight_positive"),
        # The anchors must run the right way, or the score would go backwards.
        CheckConstraint(
            "(direction = 'up_good' AND threshold_bad < threshold_ok"
            " AND threshold_ok < threshold_good)"
            " OR (direction = 'down_good' AND threshold_bad > threshold_ok"
            " AND threshold_ok > threshold_good)",
            name="thresholds_in_order",
        ),
        # One rule per KPI in general, and at most one per KPI for a given industry.
        Index(
            "uq_health_rules_general",
            "kpi_code",
            unique=True,
            postgresql_where=text("industry_code IS NULL"),
        ),
        Index(
            "uq_health_rules_industry",
            "kpi_code",
            "industry_code",
            unique=True,
            postgresql_where=text("industry_code IS NOT NULL"),
        ),
    )

    category: Mapped[str] = mapped_column(String(15))
    kpi_code: Mapped[str] = mapped_column(String(60), ForeignKey("kpi_definitions.code"))
    basis: Mapped[str] = mapped_column(String(12))
    direction: Mapped[str] = mapped_column(String(10))
    # The score is 0 at `bad`, 60 at `ok`, 100 at `good`, in a straight line between them.
    threshold_bad: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    threshold_ok: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    threshold_good: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    industry_code: Mapped[str | None] = mapped_column(String(50), ForeignKey("industries.code"))
    note: Mapped[str | None] = mapped_column(String(300))  # where the thresholds come from
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # A seasonal rule follows the trading year (sales, profit, customers), so its "usual" is the
    # same month last year or is adjusted for the business's busy and quiet seasons.
    seasonal: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class HealthCategoryWeight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "health_category_weights"
    __table_args__ = (
        CheckConstraint(_one_of("category", HEALTH_CATEGORIES), name="category_valid"),
        CheckConstraint("weight >= 0", name="weight_not_negative"),
        Index(
            "uq_health_category_weights_general",
            "category",
            unique=True,
            postgresql_where=text("industry_code IS NULL"),
        ),
        Index(
            "uq_health_category_weights_industry",
            "category",
            "industry_code",
            unique=True,
            postgresql_where=text("industry_code IS NOT NULL"),
        ),
    )

    category: Mapped[str] = mapped_column(String(15))
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    industry_code: Mapped[str | None] = mapped_column(String(50), ForeignKey("industries.code"))


class BusinessHealth(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "business_health"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_business_health_org_id"),
        UniqueConstraint(
            "organization_id", "granularity", "period_start", name="uq_business_health_period"
        ),
        CheckConstraint("granularity = 'month'", name="granularity_valid"),
        CheckConstraint(
            _one_of("status", HEALTH_STATUSES + NO_SCORE_STATUSES), name="status_valid"
        ),
        CheckConstraint(_one_of("trend", TRENDS), name="trend_valid"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        CheckConstraint(
            "overall_score IS NULL OR overall_score BETWEEN 0 AND 100", name="score_in_range"
        ),
        CheckConstraint("coverage_pct BETWEEN 0 AND 100", name="coverage_in_range"),
        CheckConstraint(
            "(overall_score IS NULL) = (status = 'not_enough_data')", name="score_matches_status"
        ),
        Index("ix_business_health_org_period", "organization_id", "period_start"),
    )

    granularity: Mapped[str] = mapped_column(String(10), server_default="month")
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    is_complete: Mapped[bool] = mapped_column(Boolean)
    overall_score: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(20))
    previous_score: Mapped[int | None] = mapped_column(SmallInteger)
    trend: Mapped[str | None] = mapped_column(String(5))
    # What share (0-100) of the weighted picture could be scored. Marketing with no data yet
    # lowers it; the score is then built from what is known, and says so.
    coverage_pct: Mapped[int] = mapped_column(SmallInteger)
    data_quality: Mapped[int | None] = mapped_column(SmallInteger)  # lowest among the KPIs used
    explanation: Mapped[str] = mapped_column(Text)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BusinessHealthComponent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "business_health_components"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "health_id"],
            ["business_health.organization_id", "business_health.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("health_id", "category", name="uq_business_health_components_category"),
        CheckConstraint(_one_of("category", HEALTH_CATEGORIES), name="category_valid"),
        CheckConstraint(
            _one_of("status", HEALTH_STATUSES + NO_SCORE_STATUSES), name="status_valid"
        ),
        CheckConstraint(_one_of("trend", TRENDS), name="trend_valid"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_in_range"),
        CheckConstraint(
            "(score IS NULL) = (status = 'not_enough_data')", name="score_matches_status"
        ),
        Index("ix_business_health_components_health", "health_id"),
    )

    health_id: Mapped[uuid.UUID] = mapped_column()
    category: Mapped[str] = mapped_column(String(15))
    score: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(20))
    previous_score: Mapped[int | None] = mapped_column(SmallInteger)
    trend: Mapped[str | None] = mapped_column(String(5))
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    # Points this area added to the overall score (its share of the weighted average).
    contribution: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    explanation: Mapped[str] = mapped_column(Text)
    # One entry per metric judged: KPI, value, baseline, score, weight, and a sentence on it.
    details: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
