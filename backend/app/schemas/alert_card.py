import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.alerts import AlertOut


class PriorityOut(BaseModel):
    level: Literal["low", "medium", "high"]
    label: str  # "High", "Medium", "Low"
    reason: str  # one sentence on why


class ChartPointOut(BaseModel):
    period_start: date
    value: str | None
    is_complete: bool


class CardChartOut(BaseModel):
    """The figure over the last months, with the month the alert is about marked."""

    code: str
    name: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    marked: date
    points: list[ChartPointOut]  # oldest first


class CardSuggestionOut(BaseModel):
    """What to do about it: the recommended option, ready to accept as it is."""

    option_id: uuid.UUID
    title: str
    description: str
    target: str | None  # the product, day, supplier... it is about
    steps: list[str]
    why_this_one: str
    effort: Literal["low", "medium", "high"]
    cost_level: Literal["none", "low", "medium", "high"]
    days_to_effect: int
    due_by: date  # the date it would get if accepted without changing it
    other_options: int  # how many other ways were considered


class CardActionOut(BaseModel):
    """The action an accepted suggestion became."""

    id: uuid.UUID
    title: str
    status: str
    status_label: str
    owner: str | None
    target_date: date | None
    check_from: date | None  # when the result starts to be checked


class NextStepOut(BaseModel):
    """For an alert with no suggestion to make: where to go to deal with it."""

    text: str
    link: str | None


CardState = Literal[
    "open",  # a suggestion is waiting for a decision
    "proposed",  # someone put it forward and it waits for approval
    "accepted",  # it became an action
    "dismissed",  # the owner decided not to act
    "good_news",  # nothing to put right
    "no_suggestion",  # we could not work out what to do (yet)
    "not_yet",  # a suggestion has not been made, and this person cannot ask for one
    "other",  # an alert that is not about a figure changing
]


class AlertCardOut(BaseModel):
    """An alert with everything needed to decide, in one place (client feedback, section 5)."""

    alert: AlertOut
    state: CardState
    event_id: uuid.UUID | None
    headline: str
    happened: str  # what happened, in everyday words
    numbers: str | None  # the figures behind it
    why_it_matters: str | None
    season_note: str | None
    tone: Literal["good", "bad", "watch", "neutral", "unknown"]
    priority: PriorityOut
    chart: CardChartOut | None
    evidence: list[str]  # up to three statements that support the suggestion
    suggestion: CardSuggestionOut | None
    action: CardActionOut | None
    dismissed_reason: str | None
    next_step: NextStepOut | None
    can_decide: bool  # this person may accept or dismiss
