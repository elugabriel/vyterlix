import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["info", "low", "medium", "high", "critical"]
AlertStatus = Literal["open", "acknowledged", "resolved"]
AlertCategory = Literal[
    "sales",
    "financial",
    "customer",
    "inventory",
    "marketing",
    "forecast",
    "action",
    "data",
    "security",
]


class AlertOut(BaseModel):
    id: uuid.UUID
    rule_code: str
    category: AlertCategory
    kpi_category: str | None
    severity: Severity
    title: str
    body: str
    link: str | None  # the screen that explains it
    status: AlertStatus
    occurrences: int  # how many times it has been seen while open
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    postponed_until: date | None = None  # put off until this day (still open, out of the way)


class AlertEventOut(BaseModel):
    kind: Literal[
        "raised", "repeated", "acknowledged", "resolved", "reopened", "postponed", "brought_back"
    ]
    user: str | None  # nobody when the system did it
    note: str | None
    created_at: datetime


class AlertDetailOut(AlertOut):
    events: list[AlertEventOut]  # oldest first


class AlertNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class AlertCountsOut(BaseModel):
    open: int  # waiting for someone: not counting the ones put off for later
    postponed: int = 0
    acknowledged: int
    resolved: int
    high_or_critical_open: int


class RuleOut(BaseModel):
    code: str
    name: str
    category: AlertCategory
    description: str
    enabled: bool
    severity: Severity
    params: dict[str, Any]
    params_help: dict[str, str]
    always_on: bool  # security alerts cannot be switched off
    customised: bool


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    severity: Severity | None = None
    params: dict[str, int | str] | None = None


class EvaluateOut(BaseModel):
    raised: int
    repeated: int
    resolved: int
    notified: int


class NotificationOut(BaseModel):
    id: uuid.UUID
    alert_id: uuid.UUID | None
    category: AlertCategory
    severity: Severity
    title: str
    body: str
    link: str | None
    read: bool
    email: Literal["none", "pending", "sent", "failed"]
    created_at: datetime


class InboxOut(BaseModel):
    unread: int
    items: list[NotificationOut]  # newest first


class PostponeIn(BaseModel):
    """Put an alert off: for a number of days, or until a day. Exactly one of the two."""

    model_config = ConfigDict(extra="forbid")

    days: int | None = Field(default=None, ge=1, le=90)
    until: date | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "PostponeIn":
        if (self.days is None) == (self.until is None):
            raise ValueError("Give either days or until (exactly one)")
        return self
