from typing import Literal

from pydantic import BaseModel

from app.schemas.alerts import Severity
from app.schemas.kpi import PlainOut

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
    plain: PlainOut | None = None  # what it means, in everyday words


class GlanceItem(BaseModel):
    """One line under "what is going well" or "what am I waiting for"."""

    id: str
    title: str
    detail: str
    link: str
    tone: Literal["good", "bad", "watch", "neutral"]
    category: str | None


class ResultItem(BaseModel):
    title: str  # the action
    outcome: Literal["successful", "partially_successful", "unsuccessful", "inconclusive"]
    outcome_label: str  # "It worked"...
    reason: str
    link: str


class ResultsGlance(BaseModel):
    """Did my previous actions help?"""

    measured: int
    successful: int
    partially_successful: int
    unsuccessful: int
    inconclusive: int
    sentence: str  # the answer in one line
    latest: list[ResultItem]  # the most recent few, newest first


class SetupGlance(BaseModel):
    done: int
    total: int
    next_section: str | None
    ready: bool


class DashboardOut(BaseModel):
    role: Literal["owner", "manager", "viewer"]
    headline: str  # the sentence that opens the screen
    going_well: list[GlanceItem]  # what is going well
    attention: list[AttentionItem]  # what needs attention today, most serious first
    more_attention: int  # how many more there are than shown
    do_first: AttentionItem | None  # what to do first: the most serious of them
    waiting_for: list[GlanceItem]  # work in progress and results still to be checked
    results: ResultsGlance  # did my previous actions help?
    health: HealthGlance | None
    figures: list[FigureGlance]
    setup: SetupGlance | None  # only until the business is set up
    unread_notifications: int
    open_actions: int
    can_act: bool  # an owner or a manager: they are given buttons, a viewer only looks
