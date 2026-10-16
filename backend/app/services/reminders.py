"""Nudges about work that is coming due, and a check-in after it is done (client feedback
15/10/2026, section 3: "remind the responsible person, check progress, ask for an outcome").

Before this nobody was told about an action until it was already late. Now the person doing it is
told two days before it is due and again on the day, and two weeks after they finish it they are
asked how it is going, so what happened (how many customers were reached, say) is recorded before
the result is measured.

Each nudge is sent once. `action_reminders` remembers which, and for which date, so moving a target
date earns a fresh reminder and the worker can run this as often as it likes.
"""

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.uk import UK_TZ, today_uk
from app.integrations.base import utcnow
from app.models.actions import ActionReminder, BusinessAction
from app.models.outcomes import FollowUpSchedule
from app.services import notifications

logger = logging.getLogger("vyterlix.reminders")

DUE_SOON_DAYS = 2  # the first reminder comes this many days before
CHECK_IN_AFTER_DAYS = 14  # and the check-in this long after it was finished
# Still to do, and not yet late (late is the Alerts page's job): accepted, started, or part done.
LIVE = ("accepted", "in_progress", "partially_completed")


def _sent(db: Session, action_id: uuid.UUID, kind: str, for_date: date) -> bool:
    return (
        db.scalars(
            select(ActionReminder.id).where(
                ActionReminder.action_id == action_id,
                ActionReminder.kind == kind,
                ActionReminder.for_date == for_date,
            )
        ).first()
        is not None
    )


def _nudge(
    db: Session,
    action: BusinessAction,
    kind: str,
    for_date: date,
    *,
    severity: str,
    title: str,
    body: str,
    sender=None,
) -> bool:
    """Tell the person doing the action, once. False if it was already sent."""
    if action.owner_user_id is None or _sent(db, action.id, kind, for_date):
        return False
    # Remember it first: a nudge that fails to send is not worth sending twice.
    db.add(
        ActionReminder(
            organization_id=action.organization_id,
            action_id=action.id,
            kind=kind,
            for_date=for_date,
            user_id=action.owner_user_id,
            sent_at=utcnow(),
        )
    )
    db.flush()
    try:
        notifications.notify_user(
            db, action.organization_id, action.owner_user_id, category="action",
            severity=severity, title=title, body=body,
            link=f"actions.html#{action.id}", sender=sender,
        )  # fmt: skip
    except Exception:
        logger.warning("Could not send a reminder about an action", exc_info=True)
    return True


def _in_days(days: int) -> str:
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def sweep(db: Session, tenant, *, today: date | None = None, sender=None) -> dict[str, int]:
    """Send whatever reminders and check-ins have come due. Safe to run as often as you like."""
    today = today or today_uk()
    counts = {"due_soon": 0, "due_today": 0, "check_in": 0}

    live = db.scalars(
        select(BusinessAction).where(
            BusinessAction.status.in_(LIVE),
            BusinessAction.target_date.is_not(None),
            BusinessAction.owner_user_id.is_not(None),
        )
    ).all()
    for action in live:
        left = (action.target_date - today).days
        if left == 0:
            if _nudge(
                db, action, "due_today", action.target_date, severity="medium",
                title=f'Due today: "{action.title}"',
                body=(
                    f'"{action.title}" is due today. If it is finished, mark it done and tell us '
                    "what you did. If it will not be ready, move the date or hand it to someone."
                ),
                sender=sender,
            ):  # fmt: skip
                counts["due_today"] += 1
        elif 0 < left <= DUE_SOON_DAYS:
            if _nudge(
                db, action, "due_soon", action.target_date, severity="low",
                title=f'Due {_in_days(left)}: "{action.title}"',
                body=(
                    f'"{action.title}" is due on {action.target_date:%d/%m/%Y}. If it is on track '
                    "you do not need to do anything. If it will not be ready, move the date or "
                    "hand it to someone."
                ),
                sender=sender,
            ):  # fmt: skip
                counts["due_soon"] += 1

    cutoff = today - timedelta(days=CHECK_IN_AFTER_DAYS)
    finished = db.scalars(
        select(BusinessAction)
        .join(FollowUpSchedule, FollowUpSchedule.action_id == BusinessAction.id)
        .where(
            BusinessAction.status == "completed",
            BusinessAction.owner_user_id.is_not(None),
            FollowUpSchedule.status == "scheduled",  # the result has not been measured yet
        )
    ).all()
    for action in finished:
        done_on = action.completed_at.astimezone(UK_TZ).date() if action.completed_at else None
        if done_on is None or done_on > cutoff:
            continue
        if _nudge(
            db, action, "check_in", done_on, severity="low",
            title=f'How is "{action.title}" going?',
            body=(
                f'It is two weeks since you finished "{action.title}". Add a short note on what '
                "has happened so far, for example how many customers you reached or what they "
                "said. It helps us judge the result fairly when the figures come in."
            ),
            sender=sender,
        ):  # fmt: skip
            counts["check_in"] += 1

    db.commit()
    return counts
