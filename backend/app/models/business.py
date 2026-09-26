"""Business profile, goals, seasons, settings and sector benchmarks (Phase 3).

UK-first (docs/CHECKLIST.md, standing rule): GBP only, country GB, UK regions and
postcodes, Europe/London, en-GB. These rules are CHECK constraints, so no code path can
store anything else. The currency/country columns exist so the schema can go
international later by relaxing a constraint, not by migrating data.
"""

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

# ITL1 regions of the UK (the ONS statistical regions), used for location and benchmarks.
UK_REGIONS = (
    "north_east",
    "north_west",
    "yorkshire_and_the_humber",
    "east_midlands",
    "west_midlands",
    "east_of_england",
    "london",
    "south_east",
    "south_west",
    "wales",
    "scotland",
    "northern_ireland",
)
# UK Companies Act size bands, by headcount: micro <10, small <50, medium <250.
BUSINESS_SIZES = ("micro", "small", "medium", "large")
BUSINESS_MODELS = ("b2c", "b2b", "b2b_and_b2c", "marketplace", "subscription")
GOAL_TYPES = (
    "increase_revenue",
    "improve_margin",
    "reduce_costs",
    "grow_customers",
    "improve_retention",
    "improve_cash_flow",
    "reduce_stock_problems",
    "other",
)
GOAL_STATUSES = ("active", "achieved", "abandoned")
TARGET_UNITS = ("gbp", "percent", "count")
SEASON_SOURCES = ("user", "detected")
# "suggested" = detected from data, waiting for the user to confirm (never applied silently).
SEASON_STATUSES = ("active", "suggested", "dismissed")

# Full UK postcode, stored upper-case with one space (e.g. "SW1A 1AA"), plus GIR 0AA.
UK_POSTCODE_REGEX = r"^([A-Z]{1,2}[0-9][A-Z0-9]? [0-9][A-Z]{2}|GIR 0AA)$"
# GB VAT number: GB + 9 digits, or GB + 12 digits (branch traders).
GB_VAT_REGEX = r"^GB([0-9]{9}|[0-9]{12})$"


def _valid_day_of_month(month_col: str, day_col: str) -> str:
    """A day that exists every year in that month (so 29 February is refused)."""
    return (
        f"{month_col} BETWEEN 1 AND 12 AND {day_col} >= 1 AND {day_col} <= "
        f"CASE WHEN {month_col} = 2 THEN 28 "
        f"WHEN {month_col} IN (4, 6, 9, 11) THEN 30 ELSE 31 END"
    )


class Industry(Base):
    """Vyterlix's own short industry list. Shared reference data, not per business."""

    __tablename__ = "industries"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class BusinessProfile(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One per organisation. The business name is `organizations.name` (single source)."""

    __tablename__ = "business_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id"),
        CheckConstraint("currency = 'GBP'", name="currency_gbp"),
        CheckConstraint("country = 'GB'", name="country_gb"),
        CheckConstraint(
            _valid_day_of_month("fiscal_year_start_month", "fiscal_year_start_day"),
            name="fiscal_year_start_valid",
        ),
        CheckConstraint("sic_code ~ '^[0-9]{5}$'", name="sic_code_format"),
        CheckConstraint(f"postcode ~ '{UK_POSTCODE_REGEX}'", name="postcode_format"),
        CheckConstraint(f"vat_number ~ '{GB_VAT_REGEX}'", name="vat_number_format"),
        CheckConstraint(_one_of("region", UK_REGIONS), name="region_valid"),
        CheckConstraint(_one_of("business_size", BUSINESS_SIZES), name="business_size_valid"),
        CheckConstraint(_one_of("business_model", BUSINESS_MODELS), name="business_model_valid"),
        CheckConstraint("team_size >= 0", name="team_size_non_negative"),
        CheckConstraint("founded_year BETWEEN 1800 AND 2100", name="founded_year_range"),
    )

    # --- required before the dashboard (decision 2026-09-26) ---
    industry_code: Mapped[str] = mapped_column(ForeignKey("industries.code"))
    currency: Mapped[str] = mapped_column(String(3), server_default="GBP")
    # Day + month, e.g. 1 April. Configurable: UK businesses choose their own year end.
    fiscal_year_start_month: Mapped[int] = mapped_column(SmallInteger)
    fiscal_year_start_day: Mapped[int] = mapped_column(SmallInteger)

    # --- optional, can be completed later ---
    sic_code: Mapped[str | None] = mapped_column(String(5))  # UK SIC 2007
    country: Mapped[str] = mapped_column(String(2), server_default="GB")
    region: Mapped[str | None] = mapped_column(String(40))
    town_city: Mapped[str | None] = mapped_column(String(100))
    postcode: Mapped[str | None] = mapped_column(String(8))
    business_size: Mapped[str | None] = mapped_column(String(10))
    business_model: Mapped[str | None] = mapped_column(String(20))
    team_size: Mapped[int | None] = mapped_column(Integer)
    # Year founded rather than "years operating", which would go stale every year.
    founded_year: Mapped[int | None] = mapped_column(SmallInteger)
    vat_registered: Mapped[bool | None] = mapped_column(Boolean)
    vat_number: Mapped[str | None] = mapped_column(String(14))

    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BusinessSettings(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One per organisation: how dates, weeks and quiet hours work for this business."""

    __tablename__ = "business_settings"
    __table_args__ = (
        UniqueConstraint("organization_id"),
        CheckConstraint("timezone = 'Europe/London'", name="timezone_uk"),
        CheckConstraint("locale = 'en-GB'", name="locale_en_gb"),
        # ISO weekday: 1 = Monday ... 7 = Sunday.
        CheckConstraint("week_start_day BETWEEN 1 AND 7", name="week_start_day_valid"),
        CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)", name="quiet_hours_pair"
        ),
    )

    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/London")
    locale: Mapped[str] = mapped_column(String(10), server_default="en-GB")
    week_start_day: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    # Non-critical alerts wait until quiet hours end (UK local time). May cross midnight.
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)


class BusinessGoal(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "business_goals"
    __table_args__ = (
        CheckConstraint(_one_of("goal_type", GOAL_TYPES), name="goal_type_valid"),
        CheckConstraint(_one_of("status", GOAL_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("target_unit", TARGET_UNITS), name="target_unit_valid"),
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        CheckConstraint(
            "(target_value IS NULL) = (target_unit IS NULL)", name="target_value_has_unit"
        ),
    )

    title: Mapped[str] = mapped_column(String(200))
    goal_type: Mapped[str] = mapped_column(String(40))
    # The KPI this goal is measured by; KPI definitions arrive in Phase 5 (no FK yet).
    kpi_code: Mapped[str | None] = mapped_column(String(100))
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_unit: Mapped[str | None] = mapped_column(String(10))
    target_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class BusinessSeason(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A yearly recurring busy or quiet period, e.g. "Christmas: +40%" (1 Dec to 5 Jan)."""

    __tablename__ = "business_seasons"
    __table_args__ = (
        CheckConstraint(_valid_day_of_month("start_month", "start_day"), name="start_valid"),
        CheckConstraint(_valid_day_of_month("end_month", "end_day"), name="end_valid"),
        CheckConstraint(_one_of("source", SEASON_SOURCES), name="source_valid"),
        CheckConstraint(_one_of("status", SEASON_STATUSES), name="status_valid"),
        CheckConstraint(
            "expected_change_pct BETWEEN -100 AND 1000", name="expected_change_pct_range"
        ),
        # Seasons the user typed in are active straight away; only detected ones are suggested.
        CheckConstraint(
            "source = 'detected' OR status <> 'suggested'", name="only_detected_are_suggested"
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    start_month: Mapped[int] = mapped_column(SmallInteger)
    start_day: Mapped[int] = mapped_column(SmallInteger)
    end_month: Mapped[int] = mapped_column(SmallInteger)  # may be before start: crosses New Year
    end_day: Mapped[int] = mapped_column(SmallInteger)
    # +40 = typically 40% busier than normal; -20 = 20% quieter.
    expected_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(10), server_default="user")
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    notes: Mapped[str | None] = mapped_column(Text)


class BusinessBenchmark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sector reference figures for comparison. Shared across businesses (not tenant data).

    Empty until target sectors are known; always cite a source.
    """

    __tablename__ = "business_benchmarks"
    __table_args__ = (
        CheckConstraint(_one_of("region", UK_REGIONS), name="region_valid"),
        CheckConstraint(_one_of("size_band", BUSINESS_SIZES), name="size_band_valid"),
        CheckConstraint("sic_code ~ '^[0-9]{5}$'", name="sic_code_format"),
        # One figure per KPI per year per segment; NULL segment parts count as "all".
        Index(
            "uq_business_benchmarks_segment",
            "industry_code",
            "sic_code",
            "region",
            "size_band",
            "kpi_code",
            "period_year",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    industry_code: Mapped[str] = mapped_column(ForeignKey("industries.code"))
    sic_code: Mapped[str | None] = mapped_column(String(5))
    region: Mapped[str | None] = mapped_column(String(40))  # None = whole UK
    size_band: Mapped[str | None] = mapped_column(String(10))
    kpi_code: Mapped[str] = mapped_column(String(100))
    period_year: Mapped[int] = mapped_column(SmallInteger)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)


LIST_KINDS = ("offering", "sales_channel", "customer_type", "cost_category")


class BusinessListItem(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A business's own lists: what it sells (offerings), where it sells (sales channels),
    who it sells to (customer types) and what it spends on (cost categories).

    Imported data will point at these items (Phase 4), so they are archived, never deleted.
    """

    __tablename__ = "business_list_items"
    __table_args__ = (
        CheckConstraint(_one_of("kind", LIST_KINDS), name="kind_valid"),
        # "Cost of sales" (UK term for COGS) only means something for cost categories.
        CheckConstraint(
            "kind = 'cost_category' OR is_cost_of_sales IS NULL", name="cost_of_sales_only_costs"
        ),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        # One "Website" per list, whatever the capitals (archived items included).
        Index(
            "uq_business_list_items_name",
            "organization_id",
            "kind",
            text("lower(name)"),
            unique=True,
        ),
    )

    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort_order: Mapped[int | None] = mapped_column(SmallInteger)
    is_cost_of_sales: Mapped[bool | None] = mapped_column(Boolean)
