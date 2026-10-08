from typing import Literal

from pydantic import BaseModel

from app.schemas.alerts import Severity

ItemKind = Literal[
    "approval", "action_overdue", "alert", "follow_up", "action_due_soon", "suggestion"
]


class AttentionItem(BaseModel):
    id: str
    kind: ItemKind
    severity: Severity
    title: str
    detail: str
    link: str  # the screen that deals with it
    category: str | None
    can_act: bool  # whether this person is given something to do about it


class HealthGlance(BaseModel):
    period: str
    score: int | None
    status: str
    previous_score: int | None
    weakest: str | None  # the area pulling it down
    explanation: str


class FigureGlance(BaseModel):
    code: str
    name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    period: str  # the month, as the first of it
    value: str | None
    change_pct: str | None
    direction: Literal["up_good", "down_good", "neutral"]


class SetupGlance(BaseModel):
    done: int
    total: int
    next_section: str | None
    ready: bool


class DashboardOut(BaseModel):
    role: Literal["owner", "manager", "viewer"]
    headline: str  # the sentence that opens the screen
    attention: list[AttentionItem]  # most serious first
    more_attention: int  # how many more there are than shown
    health: HealthGlance | None
    figures: list[FigureGlance]
    setup: SetupGlance | None  # only until the business is set up
    unread_notifications: int
    open_actions: int
    can_act: bool  # an owner or a manager: they are given buttons, a viewer only looks
