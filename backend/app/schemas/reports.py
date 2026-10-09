import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReportKind = Literal["monthly", "health", "kpi", "outcomes"]
Frequency = Literal["weekly", "monthly"]


class ScheduleOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    report_name: str
    frequency: Frequency
    weekday: int | None
    day_of_month: int | None
    when: str  # in words: "Every Monday at 7am"
    recipients: list[str]  # names
    recipient_ids: list[uuid.UUID]
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    kind: ReportKind
    title: str
    trigger: Literal["manual", "scheduled"]
    period_start: date
    period_end: date
    generated_at: datetime
    emailed: bool


class RunOut(RunSummaryOut):
    content: dict[str, Any]  # the report exactly as it was written
    rules_version: str


class ReportOut(BaseModel):
    id: uuid.UUID
    kind: ReportKind
    name: str
    description: str
    months: int  # how many months it covers (the monthly report always covers one)
    fixed_months: bool  # the number of months cannot be changed
    last_run: RunSummaryOut | None  # the latest one written for you
    schedules: list[ScheduleOut]


class MonthsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    months: Annotated[int, Field(ge=1, le=24)]


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    weekday: Annotated[int, Field(ge=1, le=7)] | None = None  # 1 = Monday
    day_of_month: Annotated[int, Field(ge=1, le=28)] | None = None
    recipients: Annotated[list[uuid.UUID], Field(min_length=1, max_length=50)]
    enabled: bool = True


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency | None = None
    weekday: Annotated[int, Field(ge=1, le=7)] | None = None
    day_of_month: Annotated[int, Field(ge=1, le=28)] | None = None
    recipients: Annotated[list[uuid.UUID], Field(min_length=1, max_length=50)] | None = None
    enabled: bool | None = None
