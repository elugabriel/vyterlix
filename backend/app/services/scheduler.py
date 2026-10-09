"""The periodic round the background worker makes: things that happen because time has passed,
not because someone pressed a button.

- work that has run past its date is marked overdue
- finished actions whose follow-up date has come are followed up (the person responsible is told,
  and the result is measured as soon as the month's figures are in)

No separate scheduler service is needed: the worker that runs the queued jobs calls this every few
minutes, and everything it does is safe to repeat.
"""

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions import rules as action_rules
from app.core.uk import today_uk
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.integrations.base import utcnow
from app.models.actions import BusinessAction
from app.models.alerts import Notification
from app.models.billing import Subscription
from app.models.identity import OrganizationUser, Role, User
from app.models.outcomes import FollowUpSchedule
from app.models.reports import ReportSchedule
from app.services import actions, alerts, billing, notifications, outcomes, reports
from app.services.jobs import JobTenant

logger = logging.getLogger("vyterlix.scheduler")


def _businesses_with_work(db: Session, today: date, now: datetime | None = None) -> set:
    due = db.scalars(
        select(FollowUpSchedule.organization_id)
        .where(FollowUpSchedule.status == "scheduled", FollowUpSchedule.due_date <= today)
        .execution_options(**ACROSS_TENANTS)
    ).all()
    open_work = db.scalars(
        select(BusinessAction.organization_id)
        .where(BusinessAction.status.in_(sorted(action_rules.OPEN)))
        .execution_options(**ACROSS_TENANTS)
    ).all()
    waiting = db.scalars(
        select(Notification.organization_id)
        .where(Notification.email_status == "pending")
        .execution_options(**ACROSS_TENANTS)
    ).all()
    reports_due = db.scalars(
        select(ReportSchedule.organization_id)
        .where(ReportSchedule.enabled.is_(True), ReportSchedule.next_run_at <= (now or utcnow()))
        .execution_options(**ACROSS_TENANTS)
    ).all()
    renewals = db.scalars(
        select(Subscription.organization_id)
        .where(
            Subscription.provider == "sandbox",
            Subscription.status == "active",
            Subscription.current_period_end <= (now or utcnow()),
        )
        .execution_options(**ACROSS_TENANTS)
    ).all()
    return set(due) | set(open_work) | set(waiting) | set(reports_due) | set(renewals)


def _owner(db: Session, organization_id) -> User | None:
    with tenant_scope(db, organization_id):
        return db.scalars(
            select(User)
            .join(OrganizationUser, OrganizationUser.user_id == User.id)
            .join(Role, Role.id == OrganizationUser.role_id)
            .where(Role.code == "owner", OrganizationUser.status == "active")
            .order_by(OrganizationUser.created_at)
        ).first()


def tick(db: Session, *, today: date | None = None, now: datetime | None = None) -> dict[str, int]:
    """One round across every business. A problem in one business never stops the others."""
    today = today or today_uk()
    totals = {
        "businesses": 0,
        "overdue": 0,
        "followed_up": 0,
        "measured": 0,
        "reports": 0,
        "renewed": 0,
    }
    for organization_id in sorted(_businesses_with_work(db, today, now), key=str):
        owner = _owner(db, organization_id)
        if owner is None:
            continue
        tenant = JobTenant(organization_id=organization_id, user=owner)
        try:
            with tenant_scope(db, organization_id):
                totals["renewed"] += billing.roll_sandbox(db, organization_id, now or utcnow())
                totals["overdue"] += actions.refresh_overdue(db, tenant, today=today)
                result = outcomes.sweep(db, tenant, today=today)
                alerts.evaluate(db, tenant, today=today, now=now)
                notifications.send_due_emails(db, now=now)
                totals["reports"] += reports.run_due(db, organization_id, now=now)
                db.commit()
            totals["followed_up"] += result["told"]
            totals["measured"] += result["measured"]
            totals["businesses"] += 1
        except Exception:
            db.rollback()
            logger.error("The scheduled round failed for %s", organization_id, exc_info=True)
    return totals
