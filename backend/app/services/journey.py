"""One change from start to finish, on one page (client feedback sections 1 and 3).

The pieces already exist, each on its own page: what happened (What changed), why (the diagnosis),
what to do (the recommendation), the decision (the alert card), the work (Actions) and how it went
(the result). This puts them in order for a single change and says which step it is on.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.permissions import Perm
from app.models.alerts import Alert
from app.schemas.journey import (
    JourneyDiagnosisOut,
    JourneyOut,
    JourneySuggestionOut,
    StepOut,
)
from app.services import actions as action_service
from app.services import alert_card, detection, diagnosis, recommendations

LABELS = {
    "happened": "What happened",
    "why": "Why it happened",
    "suggest": "What we suggest",
    "decide": "Your decision",
    "doing": "Getting it done",
    "result": "How it went",
}
ORDER = tuple(LABELS)


def _alert_id(db: Session, event_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        select(Alert.id)
        .where(Alert.rule_code.like("change_%"), Alert.data["event_id"].astext == str(event_id))
        .order_by(Alert.created_at.desc())
        .limit(1)
    )


def _diagnosis(db: Session, event_id: uuid.UUID) -> JourneyDiagnosisOut | None:
    try:
        d = diagnosis.read(db, event_id)
    except NotFoundError:
        return None
    return JourneyDiagnosisOut(
        headline=d.headline,
        summary=d.summary,
        confidence_label=d.confidence_label,
        evidence=[e.statement for e in d.evidence if e.evidence_type != "insufficient"],
        cannot_tell=[e.statement for e in d.evidence if e.evidence_type == "insufficient"],
    )


def _recommendation(db: Session, event_id: uuid.UUID):
    try:
        return recommendations.read(db, event_id)
    except NotFoundError:
        return None


def _stage(rec, action, outcome, has_diagnosis: bool) -> str:
    if outcome is not None:
        return "result"
    if action is not None:
        return "result" if action.status in ("completed", "cancelled") else "doing"
    if rec is not None and rec.status in ("open", "proposed"):
        return "decide"
    if rec is not None:
        return "suggest"
    return "suggest" if has_diagnosis else "why"


def _notes(rec, action, follow_up, outcome) -> dict[str, str | None]:
    notes: dict[str, str | None] = {k: None for k in ORDER}
    notes["suggest"] = None if rec is None else rec.headline
    if rec is not None:
        notes["decide"] = {
            "open": "Waiting for you.",
            "proposed": "Put forward, waiting for the owner to approve it.",
            "accepted": "Accepted: it became an action.",
            "dismissed": "You decided not to act on this.",
            "no_action_needed": "Nothing to decide: this is good news.",
            "insufficient_evidence": "Nothing to decide yet: we cannot say what to do.",
        }.get(rec.status)
    if action is not None:
        notes["doing"] = f"{action.status_label}" + (
            f", with {action.owner}" if action.owner else ""
        )
    if outcome is not None:
        notes["result"] = f"{outcome.label}: {outcome.reason}"
    elif follow_up is not None:
        notes["result"] = f"We will check from {follow_up.due_date:%d/%m/%Y}."
    return notes


def _states(stage: str, rec, action, outcome) -> dict[str, str]:
    index = ORDER.index(stage)
    states = {
        k: ("done" if i < index else "current" if i == index else "todo")
        for i, k in enumerate(ORDER)
    }
    if outcome is not None:
        states["result"] = "done"
    if rec is not None and rec.status in ("no_action_needed", "insufficient_evidence", "dismissed"):
        for key in ("decide", "doing", "result"):
            states[key] = "not_needed"
        states["suggest"] = "done"
        if rec.status == "dismissed":
            states["decide"] = "done"
    return states


def journey(db: Session, tenant, event_id: uuid.UUID) -> JourneyOut:
    event = detection.get_event(db, event_id)
    diag = _diagnosis(db, event_id)
    rec = _recommendation(db, event_id)
    found = alert_card._action_for(db, event_id)
    action = action_service.get_action(db, found.id) if found else None
    follow_up = action.follow_up if action else None
    outcome = action.outcome if action else None

    stage = _stage(rec, action, outcome, diag is not None)
    states, notes = _states(stage, rec, action, outcome), _notes(rec, action, follow_up, outcome)
    top = rec.options[0] if rec is not None and rec.options else None
    return JourneyOut(
        event=event,
        stage=stage,
        steps=[StepOut(key=k, label=LABELS[k], state=states[k], note=notes[k]) for k in ORDER],
        chart=alert_card._chart(db, event),
        diagnosis=diag,
        suggestion=None
        if rec is None
        else JourneySuggestionOut(
            status=rec.status,
            headline=rec.headline,
            why_this_one=rec.rationale,
            title=top.title if top else None,
            steps=list(top.intervention.steps) if top else [],
            other_options=max(len(rec.options) - 1, 0),
        ),
        action=found,
        follow_up=follow_up,
        outcome=outcome,
        alert_id=_alert_id(db, event_id),
        can_decide=tenant.can(Perm.RECOMMENDATIONS_ACTION),
    )
