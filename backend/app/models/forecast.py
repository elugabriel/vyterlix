"""The forecasting engine's tables (Phase 8).

- forecast_models: the forecasting methods Vyterlix can use, as data (code, name, a plain
  description, a version). A forecast records which method and version made it, so every number
  can be traced to the method that produced it. Global (not tenant data), like kpi_definitions.
- forecasts: one forecast of one figure (a KPI) for one business, made from the history up to
  `as_of` (the last finished month). It says which method was chosen and why, how much history it
  had, and a plain-English explanation.
- forecast_predictions: one row per future month in a forecast: the expected value and a range
  (lower to upper) the real figure should fall in `interval_level` per cent of the time. Once the
  month has finished, `actual_value` is filled in so the forecast's accuracy can be checked.
- forecast_evaluations: how each candidate method did when tried on the business's own recent
  history (a backtest: forecast a month the method has not seen, compare with what happened).
  This is why a method was chosen, and later how accurate forecasts really were.

The forecast numbers always come from the statistical methods in app/forecast, never from a
language model.
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

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

FORECAST_STATUSES = ("ok", "insufficient_data")
EVALUATION_METHODS = ("backtest", "actual")  # tried on history, or checked against what happened


class ForecastModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "forecast_models"
    __table_args__ = (
        UniqueConstraint("code", name="uq_forecast_models_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("min_history >= 1", name="min_history_positive"),
    )

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(400))  # plain English: how it works
    version: Mapped[str] = mapped_column(String(20), server_default="1")
    min_history: Mapped[int] = mapped_column(SmallInteger)  # months of history it needs
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Forecast(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_forecasts_org_id"),
        UniqueConstraint(
            "organization_id", "kpi_id", "granularity", "as_of", name="uq_forecasts_kpi_as_of"
        ),
        CheckConstraint("granularity = 'month'", name="granularity_valid"),
        CheckConstraint(_one_of("status", FORECAST_STATUSES), name="status_valid"),
        CheckConstraint("horizon BETWEEN 1 AND 12", name="horizon_range"),
        CheckConstraint("interval_level BETWEEN 50 AND 99", name="interval_level_range"),
        CheckConstraint("history_months >= 0", name="history_not_negative"),
        # A forecast with predictions says which method made them.
        CheckConstraint("status <> 'ok' OR model_id IS NOT NULL", name="ok_has_model"),
        Index("ix_forecasts_org_kpi", "organization_id", "kpi_id", "as_of"),
    )

    kpi_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kpi_definitions.id"))
    granularity: Mapped[str] = mapped_column(String(10), server_default="month")
    as_of: Mapped[date] = mapped_column(Date)  # the last finished month the forecast learned from
    horizon: Mapped[int] = mapped_column(SmallInteger)  # how many months ahead
    status: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecast_models.id"))
    model_version: Mapped[str | None] = mapped_column(String(20))
    interval_level: Mapped[int] = mapped_column(SmallInteger, server_default=text("80"))
    history_months: Mapped[int] = mapped_column(Integer)
    adjusted_for_seasons: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    explanation: Mapped[str] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForecastPrediction(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "forecast_predictions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "forecast_id"],
            ["forecasts.organization_id", "forecasts.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("forecast_id", "period_start", name="uq_forecast_predictions_period"),
        CheckConstraint("period_end >= period_start", name="period_in_order"),
        CheckConstraint("lower_value <= value AND value <= upper_value", name="range_holds_value"),
        Index("ix_forecast_predictions_forecast", "forecast_id", "period_start"),
    )

    forecast_id: Mapped[uuid.UUID] = mapped_column()
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    lower_value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    upper_value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    actual_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))  # filled in afterwards
    actual_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ForecastEvaluation(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "forecast_evaluations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "forecast_id"],
            ["forecasts.organization_id", "forecasts.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "forecast_id", "model_code", "method", name="uq_forecast_evaluations_model_method"
        ),
        CheckConstraint(_one_of("method", EVALUATION_METHODS), name="method_valid"),
        CheckConstraint("n_points >= 1", name="points_positive"),
        CheckConstraint("mae >= 0 AND rmse >= 0", name="errors_not_negative"),
        Index("ix_forecast_evaluations_forecast", "forecast_id"),
    )

    forecast_id: Mapped[uuid.UUID] = mapped_column()
    model_code: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(10))
    mae: Mapped[Decimal] = mapped_column(Numeric(24, 6))  # typical size of the miss, in the unit
    rmse: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    mape: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))  # typical miss in per cent
    n_points: Mapped[int] = mapped_column(SmallInteger)  # how many months it was tried on
    is_chosen: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
