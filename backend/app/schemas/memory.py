import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CostLevel = Literal["none", "low", "medium", "high"]
EffortLevel = Literal["low", "medium", "high"]


class MemoryItemOut(BaseModel):
    kind: str
    key: str
    title: str
    statement: str  # the fact in plain English
    data: dict[str, Any]
    source: Literal["derived", "owner"]
    updated_at: datetime


class ConstraintsIn(BaseModel):
    """The owner's limits. Leave one out (or null) for no limit."""

    model_config = ConfigDict(extra="forbid")

    max_cost_level: CostLevel | None = None
    max_effort: EffortLevel | None = None
    excluded_actions: list[Annotated[str, StringConstraints(max_length=60)]] = Field(
        default_factory=list, max_length=30
    )
    quick_results_only: bool = False


class ConstraintsOut(BaseModel):
    max_cost_level: CostLevel | None
    max_effort: EffortLevel | None
    excluded_actions: list[str]  # library codes
    excluded_names: list[str]  # and what they are called
    quick_results_only: bool
    updated_at: datetime | None


class LessonOut(BaseModel):
    action: str | None  # the kind of action, by name
    kpi_code: str
    kpi_name: str
    outcome: Literal["successful", "partially_successful", "unsuccessful", "inconclusive"]
    achieved_pct: int | None
    lesson: str
    learned_at: datetime


class PatternOut(BaseModel):
    action: str
    code: str
    kpi_code: str
    kpi_name: str
    successful: int
    partially_successful: int
    unsuccessful: int
    inconclusive: int
    average_achieved_pct: str | None
    last_outcome: str | None


class RetrievalOut(BaseModel):
    id: uuid.UUID
    purpose: str
    event_id: uuid.UUID | None
    used: list[dict[str, Any]]
    created_at: datetime


class MemoryOut(BaseModel):
    normal_ranges: list[MemoryItemOut]
    customer_patterns: list[MemoryItemOut]
    goals: list[MemoryItemOut]
    seasons: list[MemoryItemOut]
    constraints: ConstraintsOut
    lessons: list[LessonOut]
    patterns: list[PatternOut]
    recent_use: list[RetrievalOut]
    last_rebuilt: datetime | None


class RebuildOut(BaseModel):
    normal_ranges: int
    customer_patterns: int
    goals: int
    seasons: int
    removed: int
