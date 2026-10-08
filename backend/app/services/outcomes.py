# ruff: noqa: E501
"""Following up on finished actions and measuring whether they worked (Phase 11).

When an action is marked done, a follow-up is scheduled for when it should have had time to show
(see outcomes/rules.py). When that date comes, the sweep tells the person responsible and measures
the result as soon as the month's figures are in. After a result that was not a success, a different
action is suggested (the one just tried is left out), and every result feeds the track record that
the recommendation engine scores future actions by.
"""

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.uk import UK_TZ, today_uk
from app.integrations.base import utcnow
from app.models.actions import ActionEvidence, ActionUpdate, BusinessAction, BusinessIntervention
from app.models.diagnostics import DetectionEvent
from app.models.identity import User
from app.models.kpi import KpiDefinition, KpiValue
from app.models.outcomes import FollowUpSchedule, InterventionOutcome
from app.models.recommendations import Intervention
from app.outcomes import rules
from app.schemas.outcomes import (
    FollowUpOut,
    OutcomeOut,
    OutcomesSummaryOut,
    ReportOut,
    TrackRecordOut,
)
from app.services import memory, notifications, track_record
from app.services.audit import AuditAction, record_audit

logger = logging.getLogger("vyterlix.outcomes")

DEFAULT_DAYS_TO_SHOW = 30


# --- words -------------------------------------------------------------------------------------


def show(value: Decimal | None, unit: str) -> str | None:
    """An amount in the figure's own unit: pounds, percentage points, a count or a ratio."""
    if value is None:
        return None
    sign = "-" if value < 0 else ""
    number = abs(value)
    if unit == "gbp":
        return f"{sign}£{number:,.2f}"
    if unit == "percent":
        return f"{sign}{number:.1f} points"
    if unit == "count":
        return f"{sign}{number:,.0f}"
    return f"{sign}{number:.2f}"


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else str(Decimal(value).quantize(Decimal("0.01")))


def _month(day: date) -> str:
    return f"{day:%B %Y}"


def _log(db: Session, tenant, action: BusinessAction, note: str, details: dict) -> None:
    """Write the result into the action's history, as the system."""
    now = max(utcnow(), action.last_activity_at + timedelta(microseconds=1))
    db.add(
        ActionUpdate(
            organization_id=tenant.organization_id,
            action_id=action.id,
            user_id=None,
            kind="outcome",
            note=note,
            details=details,
            created_at=now,
        )
    )
    action.last_activity_at = now


# --- scheduling --------------------------------------------------------------------------------


def schedule(db: Session, tenant, action: BusinessAction) -> FollowUpSchedule:
    """Plan the check for a finished action (once)."""
    existing = db.scalars(
        select(FollowUpSchedule).where(FollowUpSchedule.intervention_id == action.intervention_id)
    ).first()
    if existing is not None:
        return existing
    intervention = db.get(BusinessIntervention, action.intervention_id)
    library = db.get(Intervention, intervention.library_id) if intervention.library_id else None
    days = library.typical_days_to_effect if library is not None else DEFAULT_DAYS_TO_SHOW
    finished = (action.completed_at or utcnow()).astimezone(UK_TZ).date()
    due = rules.follow_up_due(finished, days)
    row = FollowUpSchedule(
        organization_id=tenant.organization_id,
        intervention_id=action.intervention_id,
        action_id=action.id,
        due_date=due,
        measure_month=rules.month_to_measure(due),
        status="scheduled",
        responsible_user_id=action.owner_user_id or intervention.accepted_by_user_id,
    )
    db.add(row)
    db.flush()
    return row


def _follow_up(db: Session, intervention_id: uuid.UUID) -> FollowUpSchedule | None:
    return db.scalars(
        select(FollowUpSchedule).where(FollowUpSchedule.intervention_id == intervention_id)
    ).first()


def _follow_up_out(row: FollowUpSchedule | None, today: date) -> FollowUpOut | None:
    if row is None:
        return None
    return FollowUpOut(
        due_date=row.due_date,
        measure_month=row.measure_month,
        status=row.status,
        is_due=row.status == "scheduled" and row.due_date <= today,
        notified=row.notified_at is not None,
    )


# --- measuring ---------------------------------------------------------------------------------


def _value(db: Session, kpi_id: uuid.UUID, month: date) -> KpiValue | None:
    return db.scalars(
        select(KpiValue).where(
            KpiValue.kpi_id == kpi_id,
            KpiValue.granularity == "month",
            KpiValue.period_start == month,
            KpiValue.status == "ok",
            KpiValue.is_complete.is_(True),
            KpiValue.value.is_not(None),
        )
    ).first()


def _direction(db: Session, kpi: KpiDefinition, intervention: BusinessIntervention) -> str | None:
    """Which way is better for this figure: its own direction, or the opposite of the change that
    started it for a figure with no good or bad direction."""
    if kpi.direction != "neutral":
        return kpi.direction
    event = db.get(DetectionEvent, intervention.event_id) if intervention.event_id else None
    if event is None:
        return None
    return "up_good" if event.direction == "down" else "down_good"


def measure(
    db: Session,
    tenant,
    action_id: uuid.UUID,
    *,
    today: date | None = None,
    by_system: bool = False,
    sender=None,
) -> OutcomeOut:
    """Compare the figure now with the figure when the action was accepted."""
    today = today or today_uk()
    action = db.get(BusinessAction, action_id)
    if action is None:
        raise NotFoundError("That action was not found", code="action_not_found")
    if action.status != "completed":
        raise ConflictError(
            "Only finished actions can be followed up. Mark it as done first.", code="not_finished"
        )
    intervention = db.get(BusinessIntervention, action.intervention_id)
    if db.scalars(
        select(InterventionOutcome).where(InterventionOutcome.intervention_id == intervention.id)
    ).first():
        raise ConflictError(
            "The result of this action has already been measured.", code="already_measured"
        )
    plan = schedule(db, tenant, action)
    if today < plan.due_date:
        raise ConflictError(
            f"It is too soon to tell. It will be checked from {plan.due_date:%d/%m/%Y}.",
            code="too_early",
            details={"due_date": plan.due_date.isoformat()},
        )
    kpi = db.get(KpiDefinition, intervention.kpi_id)
    now_value = _value(db, kpi.id, plan.measure_month)
    if now_value is None:
        if (today - plan.due_date).days <= rules.GRACE_DAYS:
            raise ConflictError(
                f"The figures for {_month(plan.measure_month)} are not in yet.",
                code="no_figures_yet",
                details={"month": plan.measure_month.isoformat()},
            )
        verdict = rules.Verdict(
            "inconclusive",
            f"There are still no figures for {_month(plan.measure_month)}, so we cannot say what the action did.",
        )
        return _store(
            db,
            tenant,
            action,
            intervention,
            plan,
            kpi,
            verdict,
            None,
            None,
            None,
            None,
            by_system,
            sender,
        )

    direction = _direction(db, kpi, intervention)
    expected = (
        None
        if intervention.expected_impact_value is None
        else Decimal(intervention.expected_impact_value)
    )
    if direction is None or intervention.baseline_value is None:
        verdict = rules.Verdict(
            "inconclusive",
            "We do not know the starting figure or which way is better for this one.",
        )
        return _store(
            db,
            tenant,
            action,
            intervention,
            plan,
            kpi,
            verdict,
            now_value,
            expected,
            None,
            None,
            by_system,
            sender,
        )
    baseline = Decimal(intervention.baseline_value)
    actual = rules.improvement(direction, baseline, Decimal(now_value.value))
    seasonal = _seasonal(db, kpi.id, direction, intervention.baseline_period, plan.measure_month)
    verdict = rules.judge(
        expected=expected,
        actual=actual,
        seasonal=seasonal,
        data_quality=now_value.data_quality,
        show=lambda v: show(v, kpi.unit),
    )
    return _store(
        db,
        tenant,
        action,
        intervention,
        plan,
        kpi,
        verdict,
        now_value,
        expected,
        actual,
        seasonal,
        by_system,
        sender,
    )


def _seasonal(
    db: Session,
    kpi_id: uuid.UUID,
    direction: str,
    baseline_month: date | None,
    measured_month: date,
) -> Decimal | None:
    """What the same two months did a year earlier, counted the same way, if both are known."""
    if baseline_month is None:
        return None
    before = _value(db, kpi_id, rules.a_year_earlier(baseline_month))
    after = _value(db, kpi_id, rules.a_year_earlier(measured_month))
    if before is None or after is None:
        return None
    return rules.improvement(direction, Decimal(before.value), Decimal(after.value))


def _store(
    db,
    tenant,
    action,
    intervention,
    plan,
    kpi,
    verdict,
    now_value,
    expected,
    actual,
    seasonal,
    by_system,
    sender=None,
) -> OutcomeOut:  # noqa: ANN001
    outcome = InterventionOutcome(
        organization_id=tenant.organization_id,
        intervention_id=intervention.id,
        outcome=verdict.outcome,
        reason=verdict.reason,
        baseline_period=intervention.baseline_period,
        baseline_value=intervention.baseline_value,
        measured_period=plan.measure_month if now_value is not None else None,
        measured_value=None if now_value is None else now_value.value,
        expected_change=expected,
        actual_change=actual,
        seasonal_change=seasonal,
        adjusted_change=verdict.adjusted,
        achieved_pct=verdict.achieved_pct,
        data_quality=None if now_value is None else now_value.data_quality,
        rules_version=rules.RULES_VERSION,
        details={"kpi": kpi.code, "due_date": plan.due_date.isoformat()},
        measured_at=utcnow(),
        measured_by_user_id=None if by_system else tenant.user.id,
    )
    db.add(outcome)
    plan.status = "done"
    _log(
        db, tenant, action, f"{rules.OUTCOME_LABELS[verdict.outcome]}. {verdict.reason}",
        {"outcome": verdict.outcome, "achieved_pct": verdict.achieved_pct},
    )  # fmt: skip
    record_audit(
        db,
        AuditAction.OUTCOME_MEASURED,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="action",
        target_id=action.id,
        ip_address=None,
        user_agent=None,
        details={"outcome": verdict.outcome},
    )
    db.commit()
    try:
        memory.learn(db, tenant, intervention.id)  # keep what this result taught us
    except Exception:
        db.rollback()
        logger.warning("Could not record what was learned", exc_info=True)
    alternative = None
    if verdict.outcome in ("partially_successful", "unsuccessful"):
        alternative = _suggest_alternative(db, tenant, intervention)
        if alternative:
            outcome.details = {**outcome.details, "alternative": alternative}
            db.commit()
    if by_system:
        _tell(db, plan, action, verdict, alternative, sender)
    return _outcome_out(db, outcome)


def _suggest_alternative(db: Session, tenant, intervention: BusinessIntervention) -> str | None:
    """After a result that was not a success, work the recommendation out again without the action
    just tried. Returns the title of the new first choice, if there is one."""
    if intervention.event_id is None:
        return None
    from app.services import recommendations

    try:
        result = recommendations.generate(db, tenant, intervention.event_id)
    except Exception:
        db.rollback()
        logger.warning("Could not suggest another action", exc_info=True)
        return None
    if result.status != "open" or not result.options:
        return None
    return result.options[0].title


# --- telling people ------------------------------------------------------------------------------


def _tell(
    db: Session, plan: FollowUpSchedule, action: BusinessAction, verdict, alternative, sender=None
) -> None:
    """Hand the result to the notification service, which decides how the person is told."""
    text = f"{rules.OUTCOME_LABELS[verdict.outcome]}: {verdict.reason}"
    if alternative:
        text += f"\n\nWe now suggest something different: {alternative}."
    try:
        notifications.notify_user(
            db, action.organization_id, plan.responsible_user_id, category="action",
            severity="medium", title=f'The result of "{action.title}"', body=text,
            link=f"actions.html#{action.id}", sender=sender,
        )  # fmt: skip
    except Exception:
        logger.warning("Could not tell anyone the result", exc_info=True)


def _tell_due(db: Session, plan: FollowUpSchedule, action: BusinessAction, sender=None) -> None:
    try:
        notifications.notify_user(
            db, action.organization_id, plan.responsible_user_id, category="action",
            severity="medium", title=f'Time to check "{action.title}"',
            body=(
                f'It has been long enough to see whether "{action.title}" worked. We will compare '
                f"{_month(plan.measure_month)} with the month you accepted it as soon as the "
                "figures are in."
            ),
            link=f"actions.html#{action.id}", sender=sender,
        )  # fmt: skip
    except Exception:
        logger.warning("Could not tell anyone the follow-up is due", exc_info=True)
    plan.notified_at = utcnow()


# --- the sweep -----------------------------------------------------------------------------------


def sweep(db: Session, tenant, *, today: date | None = None, sender=None) -> dict[str, int]:
    """Follow up everything that has come due: tell the person responsible once, and measure the
    result as soon as the figures are in. Safe to run as often as you like."""
    today = today or today_uk()
    plans = db.scalars(
        select(FollowUpSchedule)
        .where(FollowUpSchedule.status == "scheduled", FollowUpSchedule.due_date <= today)
        .order_by(FollowUpSchedule.due_date)
    ).all()
    told = measured = waiting = 0
    for plan in plans:
        action = db.get(BusinessAction, plan.action_id)
        if action is None:
            continue
        if plan.notified_at is None:
            _tell_due(db, plan, action, sender)
            told += 1
            db.commit()
        try:
            measure(db, tenant, action.id, today=today, by_system=True, sender=sender)
            measured += 1
        except ConflictError as exc:
            if exc.code != "no_figures_yet":
                raise
            waiting += 1
    return {"due": len(plans), "told": told, "measured": measured, "waiting": waiting}


# --- reading ---------------------------------------------------------------------------------------


def _outcome_out(db: Session, row: InterventionOutcome) -> OutcomeOut:
    intervention = db.get(BusinessIntervention, row.intervention_id)
    kpi = db.get(KpiDefinition, intervention.kpi_id)
    by = db.get(User, row.measured_by_user_id) if row.measured_by_user_id else None
    unit = kpi.unit
    return OutcomeOut(
        outcome=row.outcome, label=rules.OUTCOME_LABELS[row.outcome], reason=row.reason,
        kpi_name=kpi.name, unit=unit, baseline_period=row.baseline_period,
        baseline_value=_plain(row.baseline_value), measured_period=row.measured_period,
        measured_value=_plain(row.measured_value), expected_change=_plain(row.expected_change),
        actual_change=_plain(row.actual_change), seasonal_change=_plain(row.seasonal_change),
        adjusted_change=_plain(row.adjusted_change), achieved_pct=row.achieved_pct,
        data_quality=row.data_quality, measured_at=row.measured_at,
        measured_by=by.full_name if by else None, alternative=row.details.get("alternative"),
    )  # fmt: skip


def outcome_for(db: Session, intervention_id: uuid.UUID) -> OutcomeOut | None:
    row = db.scalars(
        select(InterventionOutcome).where(InterventionOutcome.intervention_id == intervention_id)
    ).first()
    return None if row is None else _outcome_out(db, row)


def follow_up_for(db: Session, intervention_id: uuid.UUID, today: date | None = None):
    return _follow_up_out(_follow_up(db, intervention_id), today or today_uk())


def summary(db: Session, today: date | None = None) -> OutcomesSummaryOut:
    today = today or today_uk()
    plans = db.scalars(select(FollowUpSchedule).where(FollowUpSchedule.status == "scheduled")).all()
    counts = {name: 0 for name in rules.OUTCOME_LABELS}
    for (name,) in db.execute(select(InterventionOutcome.outcome)):
        counts[name] += 1
    names = {i.code: i.name for i in db.scalars(select(Intervention))}
    lines = [
        TrackRecordOut(
            code=code, name=names.get(code, code), successful=r.successful,
            partially_successful=r.partially_successful, unsuccessful=r.unsuccessful,
            inconclusive=r.inconclusive, score=r.score,
        )
        for code, r in sorted(track_record.load(db).items())
    ]  # fmt: skip
    return OutcomesSummaryOut(
        waiting=len(plans),
        due=sum(1 for p in plans if p.due_date <= today),
        track_record=lines,
        **counts,
    )


def report(db: Session, action_id: uuid.UUID, today: date | None = None) -> ReportOut:
    """One action's whole story: what was decided and why, what was done, and what came of it."""
    action = db.get(BusinessAction, action_id)
    if action is None:
        raise NotFoundError("That action was not found", code="action_not_found")
    today = today or today_uk()
    i = db.get(BusinessIntervention, action.intervention_id)
    kpi = db.get(KpiDefinition, i.kpi_id)
    people = {
        u.id: u.full_name
        for u in db.scalars(
            select(User).where(User.id.in_({action.owner_user_id, i.accepted_by_user_id} - {None}))
        )
    }
    notes = [
        f"{u.created_at.astimezone(UK_TZ):%d/%m/%Y}: {u.note}"
        for u in db.scalars(
            select(ActionUpdate)
            .where(ActionUpdate.action_id == action.id, ActionUpdate.kind == "note")
            .order_by(ActionUpdate.created_at)
        )
        if u.note
    ]
    evidence = len(
        db.scalars(select(ActionEvidence.id).where(ActionEvidence.action_id == action.id)).all()
    )
    outcome = outcome_for(db, i.id)
    plan = _follow_up_out(_follow_up(db, i.id), today)
    done = sum(1 for s in action.steps if s.get("done"))
    return ReportOut(
        action_id=action.id, title=action.title, kpi_name=kpi.name, unit=kpi.unit,
        accepted_by=people.get(i.accepted_by_user_id), accepted_at=i.accepted_at,
        owner=people.get(action.owner_user_id), started=action.start_date,
        finished=action.completed_at, steps_done=done, steps_total=len(action.steps),
        why=i.basis.get("rationale") or i.basis.get("headline") or "",
        expected_impact_value=_plain(i.expected_impact_value), follow_up=plan, outcome=outcome,
        notes=notes, evidence_count=evidence,
        summary=_summary(action, i, kpi, outcome, plan, done),
    )  # fmt: skip


def _summary(action, i, kpi, outcome, plan, done) -> str:
    text = (
        f'"{action.title}" was accepted on {i.accepted_at.astimezone(UK_TZ):%d/%m/%Y} to improve '
        f"{kpi.name}, which was {show(Decimal(i.baseline_value), kpi.unit) if i.baseline_value is not None else 'unknown'} "
        f"in {_month(i.baseline_period) if i.baseline_period else 'the month before'}. "
    )
    text += f"{done} of {len(action.steps)} steps were ticked off. " if action.steps else ""
    if action.status != "completed":
        return (
            text + f"It is {action.status.replace('_', ' ')}, so there is no result to measure yet."
        )
    if outcome is not None:
        return text + f"{outcome.label}. {outcome.reason}"
    if plan is not None:
        return (
            text
            + f"It will be checked from {plan.due_date:%d/%m/%Y}, using {_month(plan.measure_month)}."
        )
    return text
