# ruff: noqa: E501
"""The front screen: what needs attention today, then how the business is doing.

Everything here is read from results that other parts of the system already worked out (alerts,
actions, follow-ups, suggestions, business health, the key figures, set-up progress). Nothing is
worked out again, and what each person is shown depends on their role and, for a Manager, their area.
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts.rules import RANK
from app.core.uk import today_uk
from app.dashboard import rules
from app.models.actions import BusinessAction, BusinessIntervention
from app.models.alerts import Notification
from app.models.outcomes import FollowUpSchedule, InterventionOutcome
from app.outcomes.rules import OUTCOME_LABELS
from app.schemas.dashboard import (
    AttentionItem,
    DashboardOut,
    FigureGlance,
    GlanceItem,
    HealthGlance,
    ResultItem,
    ResultsGlance,
    SetupGlance,
)
from app.services import actions as actions_service
from app.services import alerts as alerts_service
from app.services import detection, health, kpi, onboarding, recommendations

logger = logging.getLogger("vyterlix.dashboard")


def _remit(tenant) -> list[str] | None:
    remit = tenant.remit
    return (
        None
        if remit is None or remit.kpi_categories is None
        else [str(c) for c in remit.kpi_categories]
    )


def _alert_items(db: Session) -> list[rules.Item]:
    return [
        rules.Item(
            "alert",
            a.severity,
            a.title,
            a.body,
            # a figure that changed opens its card (what it means and what to do); the rest go
            # straight to the page that deals with them
            f"alerts.html#{a.id}" if a.rule_code.startswith("change_") else a.link or "alerts.html",
            a.kpi_category,
            f"alert:{a.id}",
            {"alert_id": str(a.id)},
        )  # fmt: skip
        for a in alerts_service.list_alerts(db, status="open", limit=200)
        if RANK[a.severity] >= RANK["medium"]  # the smaller ones are on the Alerts page
        and a.postponed_until is None  # one put off comes back by itself on its day
    ]


def _action_items(db: Session, tenant, today: date) -> list[rules.Item]:
    items = []
    for a in actions_service.list_actions(db, tenant, open_only=True, today=today):
        if a.status == "overdue":
            items.append(
                rules.Item(
                    "action_overdue",
                    "medium" if a.days_late < 14 else "high",
                    f'"{a.title}" is {a.days_late} days late',
                    f"It was due on {a.target_date:%d/%m/%Y}. Move the date, finish it or cancel it.",
                    f"actions.html#{a.id}",
                    a.category,
                    f"action:{a.id}",
                )
            )
        elif a.due_soon and a.target_date is not None:
            days = (a.target_date - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            items.append(
                rules.Item(
                    "action_due_soon",
                    "low",
                    f'"{a.title}" is due {when}',
                    f"Finish by {a.target_date:%d/%m/%Y}.",
                    f"actions.html#{a.id}",
                    a.category,
                    f"action:{a.id}",
                )
            )
    return items


def _approvals(db: Session, tenant, today: date) -> list[rules.Item]:
    """Suggestions made outside someone's area, waiting for the owner."""
    if tenant.role != "owner":
        return []
    return [
        rules.Item(
            "approval",
            "medium",
            f'Approve "{a.title}"?',
            "Someone suggested this outside their own area, so it waits for you.",
            f"actions.html#{a.id}",
            a.category,
            f"approve:{a.id}",
        )
        for a in actions_service.list_actions(db, tenant, status="pending", today=today)
    ]


def _follow_ups(db: Session, today: date) -> list[rules.Item]:
    from app.models.actions import BusinessAction
    from app.models.outcomes import FollowUpSchedule

    rows = db.execute(
        select(FollowUpSchedule, BusinessAction)
        .join(BusinessAction, BusinessAction.id == FollowUpSchedule.action_id)
        .where(FollowUpSchedule.status == "scheduled", FollowUpSchedule.due_date <= today)
    ).all()
    return [
        rules.Item(
            "follow_up",
            "medium",
            f'Time to check "{a.title}"',
            f"It has been long enough to see whether it worked. We will compare {p.measure_month:%B %Y} with the month you accepted it, as soon as the figures are in.",
            f"actions.html#{a.id}",
            a.category,
            f"follow:{a.id}",
        )
        for p, a in rows
    ]


def _suggestions(db: Session) -> list[rules.Item]:
    items = []
    for r in recommendations.list_recommendations(db, "open"):
        event = detection.get_event(db, r.event_id)  # open ones are always about bad news
        items.append(
            rules.Item(
                "suggestion",
                "low",
                r.headline,
                f"Suggested: {r.recommended}."
                if r.recommended
                else "Open What changed to see the options.",
                f"changes.html#{event.id}",
                event.category,
                f"suggest:{r.id}",
                {"event_id": str(event.id)},
            )
        )
    return items


def _health(db: Session) -> HealthGlance | None:
    h = health.latest_health(db)
    if h is None or h.overall_score is None:
        return None
    scored = [c for c in h.components if c.score is not None]
    weakest = min(scored, key=lambda c: c.score).label if scored else None
    return HealthGlance(
        period=h.period_start.isoformat(),
        score=h.overall_score,
        status=h.status,
        previous_score=h.previous_score,
        weakest=weakest,
        explanation=h.explanation,
    )


def _figures(db: Session, tenant, role: str, remit: list[str] | None) -> list[FigureGlance]:
    wanted = rules.HEADLINE_FIGURES
    out = []
    by_code = {k.code: k for k in kpi.list_kpis(db, "month").kpis}
    for code in wanted:
        k = by_code.get(code)
        if k is None or k.latest is None or not rules.visible(role, remit, k.category):
            continue
        out.append(
            FigureGlance(
                code=code,
                name=k.name,
                unit=k.unit,
                period=k.latest.period_start.isoformat(),
                value=k.latest.value,
                change_pct=k.latest.change_pct,
                direction=k.direction,
                plain=k.plain,
            )
        )
    return out


def _setup(db: Session) -> SetupGlance | None:
    p = onboarding.progress(db)
    if p["ready_for_dashboard"] and p["completed_at"] is not None:
        return None
    return SetupGlance(
        done=p["done"],
        total=p["total"],
        next_section=p["next_section"],
        ready=p["ready_for_dashboard"],
    )


GOING_WELL_FIGURES = 3
GOING_WELL_RESULTS = 2
GOING_WELL_DAYS = 60  # a result is still good news this long after it was measured
WAITING_SHOWN = 6
RESULTS_SHOWN = 3
STILL_TO_DO = ("accepted", "in_progress", "partially_completed")


def _going_well(db: Session, role: str, remit, figures: list[FigureGlance], today: date):
    """What is going well, from results already worked out: health up, figures in your favour, and
    actions that worked. Said in the same plain words as everywhere else."""
    items: list[GlanceItem] = []
    h = health.latest_health(db)
    if h is not None and h.overall_score is not None and h.previous_score is not None:
        if h.overall_score > h.previous_score:
            items.append(
                GlanceItem(
                    id="health", title="Your business health went up",
                    detail=f"From {h.previous_score} to {h.overall_score} out of 100.",
                    link="health.html", tone="good", category=None,
                )
            )  # fmt: skip
    good = [f for f in figures if f.plain is not None and f.plain.tone == "good"]
    for f in good[:GOING_WELL_FIGURES]:
        items.append(
            GlanceItem(
                id=f"figure:{f.code}", title=f.name, detail=f.plain.says,
                link=f"kpis.html#{f.code}", tone="good", category=None,
            )
        )  # fmt: skip
    since = today.toordinal() - GOING_WELL_DAYS
    rows = db.execute(
        select(InterventionOutcome, BusinessIntervention, BusinessAction)
        .join(BusinessIntervention, BusinessIntervention.id == InterventionOutcome.intervention_id)
        .join(BusinessAction, BusinessAction.intervention_id == BusinessIntervention.id)
        .where(InterventionOutcome.outcome == "successful")
        .order_by(InterventionOutcome.measured_at.desc())
    ).all()
    wins = [
        (o, i, a)
        for o, i, a in rows
        if rules.visible(role, remit, i.category) and o.measured_at.date().toordinal() >= since
    ]
    for o, i, a in wins[:GOING_WELL_RESULTS]:
        items.append(
            GlanceItem(
                id=f"result:{a.id}", title=f'It worked: "{a.title}"', detail=o.reason,
                link=f"actions.html#{a.id}", tone="good", category=i.category,
            )
        )  # fmt: skip
    return items


def _waiting_for(db: Session, tenant, role: str, remit, today: date) -> list[GlanceItem]:
    """Work that is under way, work waiting for an owner's approval (for a manager), and results
    that are not due to be checked yet."""
    items: list[tuple[date, GlanceItem]] = []
    for a in actions_service.list_actions(db, tenant, open_only=True, today=today):
        if not rules.visible(role, remit, a.category):
            continue
        who = a.owner.name if a.owner else "Nobody yet"
        due = f"due {a.target_date:%d/%m/%Y}" if a.target_date else "no date set"
        if a.status in STILL_TO_DO:
            items.append(
                (
                    a.target_date or date.max,
                    GlanceItem(
                        id=f"action:{a.id}", title=a.title, detail=f"{who}, {due}.",
                        link=f"actions.html#{a.id}", tone="watch" if a.due_soon else "neutral",
                        category=a.category,
                    ),
                )
            )  # fmt: skip
        elif a.status == "pending" and role == "manager":
            items.append(
                (
                    date.max,
                    GlanceItem(
                        id=f"approval:{a.id}", title=a.title,
                        detail="Waiting for the owner to approve it.",
                        link=f"actions.html#{a.id}", tone="neutral", category=a.category,
                    ),
                )
            )  # fmt: skip
    plans = db.execute(
        select(FollowUpSchedule, BusinessAction)
        .join(BusinessAction, BusinessAction.id == FollowUpSchedule.action_id)
        .where(FollowUpSchedule.status == "scheduled", FollowUpSchedule.due_date > today)
        .order_by(FollowUpSchedule.due_date)
    ).all()
    for p, a in plans:
        if rules.visible(role, remit, a.category):
            items.append(
                (
                    p.due_date,
                    GlanceItem(
                        id=f"result:{a.id}", title=f'Result of "{a.title}"',
                        detail=f"We will check how it went from {p.due_date:%d/%m/%Y}.",
                        link=f"actions.html#{a.id}", tone="neutral", category=a.category,
                    ),
                )
            )  # fmt: skip
    items.sort(key=lambda pair: pair[0])
    return [item for _, item in items[:WAITING_SHOWN]]


def _results(db: Session, role: str, remit) -> ResultsGlance:
    """Did my previous actions help?"""
    rows = [
        (o, i, a)
        for o, i, a in db.execute(
            select(InterventionOutcome, BusinessIntervention, BusinessAction)
            .join(
                BusinessIntervention, BusinessIntervention.id == InterventionOutcome.intervention_id
            )
            .join(BusinessAction, BusinessAction.intervention_id == BusinessIntervention.id)
            .order_by(InterventionOutcome.measured_at.desc())
        ).all()
        if rules.visible(role, remit, i.category)
    ]
    by = {k: sum(1 for o, _, _ in rows if o.outcome == k) for k in OUTCOME_LABELS}
    return ResultsGlance(
        measured=len(rows),
        successful=by["successful"],
        partially_successful=by["partially_successful"],
        unsuccessful=by["unsuccessful"],
        inconclusive=by["inconclusive"],
        sentence=rules.results_sentence(
            len(rows), by["successful"], by["partially_successful"], by["unsuccessful"], by["inconclusive"]
        ),
        latest=[
            ResultItem(
                title=a.title, outcome=o.outcome, outcome_label=OUTCOME_LABELS[o.outcome],
                reason=o.reason, link=f"actions.html#{a.id}",
            )
            for o, _, a in rows[:RESULTS_SHOWN]
        ],
    )  # fmt: skip


def read(db: Session, tenant, *, today: date | None = None) -> DashboardOut:
    today = today or today_uk()
    role, remit = tenant.role, _remit(tenant)
    everything = [
        *_alert_items(db),
        *_action_items(db, tenant, today),
        *_approvals(db, tenant, today),
        *_follow_ups(db, today),
        *_suggestions(db),
    ]
    mine = [i for i in everything if rules.visible(role, remit, i.category)]
    # A viewer is told what is going on, but not about things that wait on a decision
    if role == "viewer":
        mine = [i for i in mine if i.kind not in ("approval", "suggestion")]
    shown, more = rules.top(mine)
    serious = sum(1 for i in mine if RANK[i.severity] >= RANK["high"])
    unread = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == tenant.user.id,
            Notification.in_app.is_(True),
            Notification.read_at.is_(None),
        )
    )
    counts = actions_service.counts(db, tenant, today)
    figures = _figures(db, tenant, role, remit)
    attention = [
        AttentionItem(
            id=i.id,
            kind=i.kind,
            severity=i.severity,
            title=i.title,
            detail=i.detail,
            link=i.link,
            category=i.category,
            can_act=rules.can_act(role, remit, i.category),
        )
        for i in shown
    ]
    return DashboardOut(
        role=role, headline=rules.greeting_line(len(mine), serious),
        going_well=_going_well(db, role, remit, figures, today),
        attention=attention, do_first=attention[0] if attention else None,
        waiting_for=_waiting_for(db, tenant, role, remit, today), results=_results(db, role, remit),
        more_attention=more, health=_health(db), figures=figures, setup=_setup(db) if role == "owner" else None,
        unread_notifications=unread or 0, open_actions=counts.open, can_act=role in ("owner", "manager"),
    )  # fmt: skip
