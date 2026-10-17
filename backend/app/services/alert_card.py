# ruff: noqa: E501
"""An alert and what to do about it, in one place (client feedback 15/10/2026, section 5).

Before this an alert only pointed at a page that explained it, and the recommendation and the
action were somewhere else again. A card joins them: what happened in everyday words, why it
matters, how urgent, a small chart, the suggested next step, and the buttons to decide.

The suggestion is worked out the first time someone who may decide opens the card (it is the same
recommendation the What changed page makes, and the two share it). Someone who may only look sees
the card without a suggestion, and nothing is created for them.

Accepting or dismissing from the card uses the same services as everywhere else, so the same rules
apply (a manager outside their area can only propose it) and the action is linked to the change.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions import rules as action_rules
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import KpiCategory, Perm
from app.core.uk import today_uk
from app.kpi.measures import has_records
from app.models.actions import BusinessAction, BusinessIntervention
from app.models.kpi import KpiDefinition
from app.plain.changes import explain_change
from app.plain.priority import LABEL, prioritise
from app.schemas.actions import AcceptIn, DismissIn
from app.schemas.alert_card import (
    AlertCardOut,
    CardActionOut,
    CardChartOut,
    CardSuggestionOut,
    ChartPointOut,
    NextStepOut,
    PriorityOut,
)
from app.schemas.recommendations import RecommendationOut
from app.services import actions as action_service
from app.services import alerts as alert_service
from app.services import detection, kpi, recommendations
from app.services.auth import RequestMeta

CHART_MONTHS = 12
EVIDENCE_SHOWN = 3

# What to add so a suggestion can be made, by kind of record a figure needs
NEEDS_RECORDS = {
    "sales": "Add your sales (Your data), so we can see what is behind the change.",
    "expenses": "Add your costs (Your data), so we can see what is behind the change.",
    "customer_sales": "Say which customer bought on each sale, so we can see who has stopped coming back.",
    "stock": "Add your stock records (deliveries and what you started with), so we can see what is selling slowly.",
    "bookings": "Add your appointments (Your data, then Type in data), so we can see what is behind the change.",
}
# What an admission of "not enough evidence" on the diagnosis means for the owner to do
FROM_REASON = {
    "no_product_detail": "Record which product was sold on each sale, so we can tell which products moved.",
    "spread_widely": "The change is spread across many things, so no single one stands out. A few more months of figures will help us see a pattern.",
    "not_explainable": "This figure cannot yet be split into causes, so we can only suggest things that act on the figure itself.",
}


def what_is_missing(db: Session, event, rec) -> list[str]:
    """In plain words, what is stopping us suggesting something. Never empty: if nothing is missing
    from the records, it says we do not have a suggestion for this kind of change yet."""
    found: list[str] = []
    definition = db.scalars(
        select(KpiDefinition).where(KpiDefinition.code == event.kpi_code)
    ).first()
    for dataset in definition.requires if definition else []:
        if not has_records(db, dataset) and dataset in NEEDS_RECORDS:
            found.append(NEEDS_RECORDS[dataset])
    if event.data_quality is not None and event.data_quality < 80:
        found.append(
            f"Some of the figures behind {event.period_start:%B %Y} are missing, so we are less sure what to do. See Your data for what to fill in."
        )
    for item in rec.evidence if rec is not None else []:
        reason = (item.data or {}).get("reason")
        if (
            item.evidence_type == "insufficient"
            and reason in FROM_REASON
            and FROM_REASON[reason] not in found
        ):
            found.append(FROM_REASON[reason])
    return found or ["We do not have a suggestion for this kind of change yet."]


# Where to go for an alert that is not about a figure changing (rule code -> what to say).
NEXT_STEPS = {
    "data_stale": "Bring in your latest sales and costs so the figures are up to date.",
    "data_quality": "Fix the problems in your data so the figures can be trusted.",
    "action_overdue": "Open the action and either finish it, move its date or hand it to someone.",
    "action_due_soon": "Open the action and check it is on track.",
    "forecast_decline": "Open the forecast to see what is expected and what to stock.",
    "billing_status": "Open Billing to see your plan and what to do.",
}


def _event_id(alert) -> uuid.UUID | None:
    raw = (alert.data or {}).get("event_id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _chart(db: Session, event) -> CardChartOut | None:
    try:
        history = kpi.kpi_history(db, event.kpi_code, "month", CHART_MONTHS)
    except NotFoundError:  # a chart is a nicety: the card works without it
        return None
    return CardChartOut(
        code=history.code,
        name=history.name,
        unit=history.unit,
        marked=event.period_start,
        points=[
            ChartPointOut(period_start=v.period_start, value=v.value, is_complete=v.is_complete)
            for v in history.values
            if v.status == "ok"
        ],
    )


def _action_for(db: Session, event_id: uuid.UUID) -> CardActionOut | None:
    found = db.scalars(
        select(BusinessAction)
        .join(BusinessIntervention, BusinessIntervention.id == BusinessAction.intervention_id)
        .where(BusinessIntervention.event_id == event_id)
        .order_by(BusinessAction.last_activity_at.desc())
    ).first()
    if found is None:
        return None
    action = action_service.get_action(db, found.id)
    return CardActionOut(
        id=action.id,
        title=action.title,
        status=action.status,
        status_label=action.status_label,
        owner=action.owner.name if action.owner else None,
        target_date=action.target_date,
        check_from=action.follow_up.due_date if action.follow_up else action.target_date,
    )


def _suggestion(rec: RecommendationOut) -> CardSuggestionOut | None:
    if not rec.options:
        return None
    top = rec.options[0]
    return CardSuggestionOut(
        option_id=top.id,
        title=top.title,
        description=top.description,
        target=top.target,
        steps=list(top.intervention.steps),
        why_this_one=rec.rationale,
        effort=top.effort,
        cost_level=top.cost_level,
        days_to_effect=top.days_to_effect,
        due_by=action_rules.default_target(today_uk(), top.days_to_effect),
        other_options=len(rec.options) - 1,
    )


def card(db: Session, tenant, alert_id: uuid.UUID) -> AlertCardOut:
    detail = alert_service.get_alert(db, alert_id)
    alert = alert_service._get(db, alert_id)
    can_decide = tenant.can(Perm.RECOMMENDATIONS_ACTION)
    event_id = _event_id(alert)

    if event_id is None or not alert.rule_code.startswith("change_"):
        priority = prioritise(alert_severity=alert.severity, effect="bad", scores=None)
        return AlertCardOut(
            alert=detail,
            state="other",
            event_id=None,
            headline=alert.title,
            happened=alert.body,
            numbers=None,
            why_it_matters=None,
            season_note=None,
            tone="watch",
            priority=PriorityOut(level=priority.level, label=LABEL[priority.level], reason=priority.reason),
            chart=None,
            evidence=[],
            suggestion=None,
            action=None,
            dismissed_reason=None,
            postponed_until=detail.postponed_until,
            next_step=NextStepOut(text=NEXT_STEPS.get(alert.rule_code, "Open it to see what to do."), link=alert.link),
            can_decide=False,
        )  # fmt: skip

    event = detection.get_event(db, event_id)
    words = explain_change(
        kpi_code=event.kpi_code,
        kpi_name=event.kpi_name,
        unit=event.unit,
        direction=event.direction,
        severity=event.severity,
        effect=event.effect,
        period_start=event.period_start,
        value=event.value,
        reference_value=event.reference_value,
        change=event.change,
        change_unit=event.change_unit,
        explained_by_season=event.explained_by_season,
        kind=event.kind,
        data_quality=event.data_quality,
    )

    rec: RecommendationOut | None = None
    try:
        rec = recommendations.read(db, event_id)
    except NotFoundError:  # none made yet
        rec = None
    if rec is None and can_decide:
        rec = recommendations.generate(db, tenant, event_id)

    state = "not_yet"
    suggestion = action = None
    evidence: list[str] = []
    dismissed_reason = None
    scores = None
    if rec is not None:
        # What backs the suggestion: not the figure's own movement again, and not the admission that
        # there is not enough evidence (the card says that in its own words).
        evidence = [
            e.statement
            for e in rec.evidence
            if e.evidence_type != "insufficient" and e.statement != event.summary
        ][:EVIDENCE_SHOWN]
        if rec.status == "open":
            state, suggestion = "open", _suggestion(rec)
            top = rec.options[0] if rec.options else None
            if top is not None:
                scores = {s.key: s.score for s in top.scores}
        elif rec.status == "proposed":
            state, suggestion = "proposed", _suggestion(rec)
            action = _action_for(db, event_id)
        elif rec.status == "accepted":
            state, action = "accepted", _action_for(db, event_id)
        elif rec.status == "dismissed":
            state = "dismissed"
            reason = (alert_service.get_alert(db, alert_id).events or [None])[-1]
            dismissed_reason = reason.note if reason is not None else None
        elif rec.status == "no_action_needed":
            state = "good_news"
        else:
            state = "no_suggestion"

    if state == "open" and detail.postponed_until is not None:
        state = "postponed"
    priority = prioritise(alert_severity=alert.severity, effect=event.effect, scores=scores)
    return AlertCardOut(
        alert=detail,
        state=state,
        event_id=event_id,
        headline=words.headline,
        happened=words.happened,
        numbers=words.numbers,
        why_it_matters=words.why,
        season_note=words.season,
        tone=words.tone,
        priority=PriorityOut(level=priority.level, label=LABEL[priority.level], reason=priority.reason),
        chart=_chart(db, event),
        evidence=evidence,
        suggestion=suggestion,
        action=action,
        dismissed_reason=dismissed_reason,
        postponed_until=detail.postponed_until,
        next_step=None,
        missing=what_is_missing(db, event, rec) if state == "no_suggestion" else [],
        can_decide=can_decide and state in ("open", "postponed"),
    )  # fmt: skip


def _change_alert(db: Session, alert_id: uuid.UUID):
    alert = alert_service._get(db, alert_id)
    event_id = _event_id(alert)
    if event_id is None or not alert.rule_code.startswith("change_"):
        raise ConflictError(
            "This alert is not about a figure that changed, so there is no suggestion to act on.",
            code="no_suggestion",
        )
    return alert, event_id


def _ensure_recommendation(db: Session, tenant, event_id: uuid.UUID) -> None:
    """Deciding on a suggestion that has not been worked out yet works it out first."""
    try:
        recommendations.read(db, event_id)
    except NotFoundError:
        recommendations.generate(db, tenant, event_id)


def accept(
    db: Session, tenant, alert_id: uuid.UUID, body: AcceptIn, meta: RequestMeta
) -> AlertCardOut:
    """Accept the suggestion: it becomes an action with an owner and dates, the alert is marked as
    being dealt with, and the card now shows the action."""
    alert, event_id = _change_alert(db, alert_id)
    _ensure_recommendation(db, tenant, event_id)
    action = action_service.accept(db, tenant, event_id, body, meta)
    outside = alert.kpi_category is not None and not tenant.within_remit(
        KpiCategory(alert.kpi_category)
    )
    if (
        alert.status == "open" and not outside
    ):  # a proposal from outside their area waits for the owner
        alert_service.acknowledge(db, tenant, alert_id, f"Action created: {action.title}", meta)
    return card(db, tenant, alert_id)


def dismiss(
    db: Session, tenant, alert_id: uuid.UUID, body: DismissIn, meta: RequestMeta
) -> AlertCardOut:
    """Decide not to act: the suggestion stays on record with the reason, and the alert is closed."""
    alert, event_id = _change_alert(db, alert_id)
    _ensure_recommendation(db, tenant, event_id)
    action_service.dismiss(db, tenant, event_id, body, meta)
    if alert.status != "resolved":
        reason = f"Not acting on it: {body.reason}" if body.reason else "Decided not to act on it."
        alert_service.resolve(db, tenant, alert_id, reason, meta)
    return card(db, tenant, alert_id)


def postpone(db: Session, tenant, alert_id: uuid.UUID, body, meta: RequestMeta) -> AlertCardOut:
    """Put the alert off until a day: it leaves what needs attention and comes back by itself."""
    alert_service.postpone(db, tenant, alert_id, body, meta)
    return card(db, tenant, alert_id)


def bring_back(db: Session, tenant, alert_id: uuid.UUID, meta: RequestMeta) -> AlertCardOut:
    alert_service.bring_back(db, tenant, alert_id, meta)
    return card(db, tenant, alert_id)
