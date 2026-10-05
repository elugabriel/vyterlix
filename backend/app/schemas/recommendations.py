import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.diagnostics import DetectionOut, EvidenceOut


class InterventionOut(BaseModel):
    """An action Vyterlix knows how to suggest."""

    code: str
    name: str
    category: str
    summary: str
    steps: list[str]
    effort: Literal["low", "medium", "high"]
    cost_level: Literal["none", "low", "medium", "high"]
    typical_days_to_effect: int
    impact_share: str  # the share of the gap it usually wins back (0 to 1): a starting estimate
    impact_basis: str


class ScoreLineOut(BaseModel):
    """One of the seven things an option is scored on."""

    key: str
    label: str
    score: int  # 0-100
    weight: int
    points: float  # what it added to the total


class OptionOut(BaseModel):
    id: uuid.UUID
    rank: int
    is_recommended: bool
    title: str
    description: str
    target: str | None  # the product, day, supplier... it is about
    intervention: InterventionOut
    impact_value: str  # in the figure's own unit
    impact_unit: Literal["gbp", "percent", "count", "ratio"]
    total_score: int
    scores: list[ScoreLineOut]
    effort: Literal["low", "medium", "high"]
    cost_level: Literal["none", "low", "medium", "high"]
    days_to_effect: int


class RecommendationOut(BaseModel):
    id: uuid.UUID
    event: DetectionOut
    status: Literal[
        "open", "no_action_needed", "insufficient_evidence", "dismissed", "proposed", "accepted"
    ]
    headline: str
    rationale: str  # why this one
    rules_version: str
    generated_at: datetime
    options: list[OptionOut]  # best first; the first is the recommended one
    evidence: list[EvidenceOut]


class RecommendationSummaryOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    kpi_name: str
    period_start: date
    status: Literal[
        "open", "no_action_needed", "insufficient_evidence", "dismissed", "proposed", "accepted"
    ]
    headline: str
    recommended: str | None  # the title of the top option
    score: int | None
    generated_at: datetime
