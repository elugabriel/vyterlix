import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ActionStatus = Literal[
    "pending", "accepted", "in_progress", "partially_completed", "completed", "cancelled", "overdue"
]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
HttpsUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^https://\S+$", max_length=500)
]


# --- what comes in --------------------------------------------------------------------------------


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    done: bool = False


class AcceptIn(BaseModel):
    """Accept a recommended option. Everything is optional: leave it out to take the option as it
    was suggested, or change what you want to before accepting."""

    model_config = ConfigDict(extra="forbid")

    option_id: uuid.UUID | None = None  # default: the recommended one
    title: Title | None = None
    description: Text | None = None
    steps: list[StepIn] | None = Field(default=None, max_length=30)
    owner_user_id: uuid.UUID | None = None  # who will do it (default: you)
    start_date: date | None = None  # default: today
    target_date: date | None = None  # default: when the action usually starts to show
    note: ShortText | None = None


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ShortText | None = None


class ActionPatch(BaseModel):
    """Change an action. Only what is sent changes; send null to clear the owner or a date."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Text | None = None
    steps: list[StepIn] | None = Field(default=None, max_length=30)
    owner_user_id: uuid.UUID | None = None
    start_date: date | None = None
    target_date: date | None = None


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "in_progress", "partially_completed", "completed", "cancelled"]
    note: ShortText | None = None


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Text


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ShortText | None = None


class EvidenceIn(BaseModel):
    """A note or a link to show the work (a file is uploaded instead)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["note", "link"]
    title: Title
    note: Text | None = None
    url: HttpsUrl | None = None


# --- what goes out --------------------------------------------------------------------------------


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str


class StepOut(BaseModel):
    text: str
    done: bool


class ProgressOut(BaseModel):
    done: int
    total: int
    percent: int


class UpdateOut(BaseModel):
    """One thing that happened to the action."""

    id: uuid.UUID
    kind: str
    from_status: str | None
    to_status: str | None
    note: str | None
    details: dict
    user: PersonOut | None  # null for something the system did
    created_at: datetime


class EvidenceOut(BaseModel):
    id: uuid.UUID
    kind: Literal["note", "link", "file"]
    title: str
    note: str | None
    url: str | None
    filename: str | None
    content_type: str | None
    size_bytes: int | None
    user: PersonOut | None
    created_at: datetime


class WhatWasDecidedOut(BaseModel):
    """The decision behind the action: what was accepted, and what it was meant to change."""

    title: str
    original_title: str | None  # set when it was reworded before accepting
    modified: bool
    description: str
    target: str | None
    library_code: str | None
    kpi_code: str
    kpi_name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    baseline_period: date | None  # the month the figure was read for
    baseline_value: str | None  # and what it was
    expected_impact_value: str | None
    expected_impact_unit: str | None
    recommendation_score: int | None
    accepted_by: PersonOut | None
    accepted_at: datetime
    event_id: uuid.UUID | None


class ActionSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    category: str
    kpi_name: str
    status: ActionStatus
    status_label: str
    owner: PersonOut | None
    start_date: date | None
    target_date: date | None
    days_late: int
    due_soon: bool
    progress: ProgressOut
    last_activity_at: datetime


class ActionOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category: str
    status: ActionStatus
    status_label: str
    next_statuses: list[ActionStatus]  # what it can be moved to from here
    owner: PersonOut | None
    created_by: PersonOut | None
    approved_by: PersonOut | None
    start_date: date | None
    target_date: date | None
    completed_at: datetime | None
    days_late: int
    due_soon: bool
    steps: list[StepOut]
    progress: ProgressOut
    decision: WhatWasDecidedOut
    updates: list[UpdateOut]  # oldest first
    evidence: list[EvidenceOut]
    last_activity_at: datetime


class ActionCountsOut(BaseModel):
    pending: int
    accepted: int
    in_progress: int
    partially_completed: int
    completed: int
    cancelled: int
    overdue: int
    open: int  # still being worked on (accepted, in progress, partly done or overdue)
    due_soon: int
    mine_open: int  # open and assigned to you
