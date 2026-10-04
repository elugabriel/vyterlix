import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class PredictionOut(BaseModel):
    """What we expect for one future month, and the range it should land in."""

    period_start: date
    period_end: date
    value: str
    lower: str  # the real figure should be at least this...
    upper: str  # ...and at most this, `interval_level` per cent of the time
    actual_value: str | None  # filled in once the month has finished


class HistoryPointOut(BaseModel):
    period_start: date
    value: str


class MethodOut(BaseModel):
    code: str
    name: str
    description: str
    version: str


class EvaluationOut(BaseModel):
    """How one method did when tried on this business's own recent months."""

    model_config = {"protected_namespaces": ()}

    method: MethodOut
    kind: Literal["backtest", "actual"]
    typical_miss: str  # in the figure's unit (pounds for sales)
    typical_miss_pct: str | None
    months_tested: int
    chosen: bool


class ForecastOut(BaseModel):
    id: uuid.UUID
    kpi_code: str
    kpi_name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    status: Literal["ok", "insufficient_data"]
    as_of: date  # the last finished month the forecast learned from
    horizon: int
    interval_level: int  # per cent
    history_months: int
    adjusted_for_seasons: bool
    method: MethodOut | None
    explanation: str
    calculated_at: datetime
    predictions: list[PredictionOut]
    history: list[HistoryPointOut]  # the recent months it learned from, oldest first
    evaluations: list[EvaluationOut]


class ForecastOptionsOut(BaseModel):
    kpis: list[str]  # the figures that can be forecast


class AccuracyRowOut(BaseModel):
    """One forecast month that has since finished: what we said, and what happened."""

    period_start: date
    made_from: date  # the last finished month the forecast learned from
    months_ahead: int
    predicted: str
    lower: str
    upper: str
    actual: str
    error: str  # forecast minus actual; + means the forecast was too high
    error_pct: str | None
    within_range: bool


class AheadOut(BaseModel):
    """How the forecasts did at one distance ahead (the further ahead, the harder)."""

    months_ahead: int
    checked: int
    typical_miss_pct: str | None
    within_range_pct: str


class AccuracyOut(BaseModel):
    kpi_code: str
    kpi_name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    enough_data: bool  # false until a few forecast months have finished
    checked: int
    within_range: int
    within_range_pct: str | None
    promised_pct: int  # how often the range was meant to hold
    typical_miss: str | None  # in the figure's unit
    typical_miss_pct: str | None
    bias_pct: str | None  # + means forecasts ran too high
    headline: str
    verdict: str | None
    by_months_ahead: list[AheadOut]
    rows: list[AccuracyRowOut]  # newest first
