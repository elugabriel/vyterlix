import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.alert_card import CardActionOut, CardChartOut
from app.schemas.diagnostics import DetectionOut
from app.schemas.outcomes import FollowUpOut, OutcomeOut

StepKey = Literal["happened", "why", "suggest", "decide", "doing", "result"]
StepState = Literal["done", "current", "todo", "not_needed"]


class StepOut(BaseModel):
    key: StepKey
    label: str
    state: StepState
    note: str | None  # one line on where this step stands


class JourneyDiagnosisOut(BaseModel):
    headline: str
    summary: str
    confidence_label: Literal["high", "medium", "low", "insufficient"]
    evidence: list[str]  # what the records show and what we worked out, in order
    cannot_tell: list[str]  # what we cannot tell, said plainly


class JourneySuggestionOut(BaseModel):
    status: str  # open, proposed, accepted, dismissed, no_action_needed, insufficient_evidence
    headline: str
    why_this_one: str
    title: str | None  # the recommended option
    steps: list[str]
    other_options: int


class JourneyOut(BaseModel):
    """One change from start to finish: what happened, why, what to do, who decided, how it is going
    and how it turned out, on one page (client feedback, sections 1 and 3)."""

    event: DetectionOut
    stage: StepKey  # where it is up to now
    steps: list[StepOut]
    chart: CardChartOut | None
    diagnosis: JourneyDiagnosisOut | None
    suggestion: JourneySuggestionOut | None
    action: CardActionOut | None
    follow_up: FollowUpOut | None
    outcome: OutcomeOut | None
    alert_id: uuid.UUID | None  # the alert for this change, which carries the buttons to decide
    can_decide: bool
