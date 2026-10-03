from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal["healthy", "fair", "needs_attention", "at_risk", "not_enough_data"]
Trend = Literal["up", "down", "flat"]


class MetricOut(BaseModel):
    """One KPI judged inside an area: the evidence for the area's score."""

    kpi_code: str  # open it on the Key figures page
    name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    basis: Literal["value", "vs_baseline"]
    value: str
    baseline: str | None  # this business's usual level, when judged against it
    baseline_months: int
    compared_pct: str | None  # how far from usual, in %
    score: float  # 0-100
    weight: float
    text: str  # a plain sentence: what it was, what it is judged against, what it scored


class ComponentOut(BaseModel):
    category: str
    label: str  # "Money", "Customers"...
    score: int | None  # None when nothing in the area could be measured
    status: Status
    previous_score: int | None
    trend: Trend | None
    weight: float  # how much the area counts towards the overall score
    contribution: float | None  # points it added to the overall score
    explanation: str
    metrics: list[MetricOut]  # weakest first


class HealthOut(BaseModel):
    period_start: date
    period_end: date
    overall_score: int | None
    status: Status
    previous_score: int | None
    trend: Trend | None
    coverage_pct: int  # how much of the weighted picture could be scored
    data_quality: int | None  # the lowest data-quality score among the KPIs used
    explanation: str
    calculated_at: datetime
    components: list[ComponentOut]  # biggest weight first


class HealthPointOut(BaseModel):
    period_start: date
    overall_score: int | None
    status: Status
    trend: Trend | None
    coverage_pct: int


class HealthHistoryOut(BaseModel):
    points: list[HealthPointOut]  # oldest first
