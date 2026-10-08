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
from app.models.alerts import Notification
from app.schemas.dashboard import (
    AttentionItem,
    DashboardOut,
    FigureGlance,
    HealthGlance,
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
            a.link or "alerts.html",
            a.kpi_category,
            f"alert:{a.id}",
            {"alert_id": str(a.id)},
        )  # fmt: skip
        for a in alerts_service.list_alerts(db, status="open", limit=200)
        if RANK[a.severity] >= RANK["medium"]  # the smaller ones are on the Alerts page
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
    return DashboardOut(
        role=role, headline=rules.greeting_line(len(mine), serious),
        attention=[AttentionItem(id=i.id, kind=i.kind, severity=i.severity, title=i.title, detail=i.detail, link=i.link, category=i.category, can_act=rules.can_act(role, remit, i.category)) for i in shown],
        more_attention=more, health=_health(db), figures=_figures(db, tenant, role, remit), setup=_setup(db) if role == "owner" else None,
        unread_notifications=unread or 0, open_actions=counts.open, can_act=role in ("owner", "manager"),
    )  # fmt: skip
