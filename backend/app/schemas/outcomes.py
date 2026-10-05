import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

OutcomeName = Literal["successful", "partially_successful", "unsuccessful", "inconclusive"]


class FollowUpOut(BaseModel):
    """When a finished action will be checked, and which month's figure will be used."""

    due_date: date
    measure_month: date
    status: Literal["scheduled", "done"]
    is_due: bool  # the date has come
    notified: bool


class OutcomeOut(BaseModel):
    """What happened to the figure the action was meant to move."""

    outcome: OutcomeName
    label: str
    reason: str
    kpi_name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    baseline_period: date | None
    baseline_value: str | None
    measured_period: date | None
    measured_value: str | None
    expected_change: str | None  # all counted as positive when the business is better off
    actual_change: str | None
    seasonal_change: str | None  # what that time of year normally brings, when last year is known
    adjusted_change: str | None  # the actual change with the seasonal part taken out
    achieved_pct: int | None  # of what was expected
    data_quality: int | None
    measured_at: datetime
    measured_by: str | None  # nobody when the scheduler did it
    alternative: str | None  # the other action now suggested, after a result that was not a success


class TrackRecordOut(BaseModel):
    """How one kind of action has worked for this business."""

    code: str
    name: str
    successful: int
    partially_successful: int
    unsuccessful: int
    inconclusive: int
    score: int  # out of 100: what the recommendation engine uses (50 when there is no record)


class OutcomesSummaryOut(BaseModel):
    waiting: int  # finished actions whose check has not happened yet
    due: int  # of those, the ones whose date has come
    successful: int
    partially_successful: int
    unsuccessful: int
    inconclusive: int
    track_record: list[TrackRecordOut]


class ReportOut(BaseModel):
    """The whole story of one action on one page."""

    action_id: uuid.UUID
    title: str
    kpi_name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    accepted_by: str | None
    accepted_at: datetime
    owner: str | None
    started: date | None
    finished: datetime | None
    steps_done: int
    steps_total: int
    why: str  # the recommendation's reason at the time
    expected_impact_value: str | None
    follow_up: FollowUpOut | None
    outcome: OutcomeOut | None
    notes: list[str]  # what people wrote while doing it
    evidence_count: int
    summary: str  # a plain-English paragraph
