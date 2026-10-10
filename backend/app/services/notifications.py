# ruff: noqa: E501
"""The notification service: the only place that decides who is told about something, how, and when.

Other parts of the system raise alerts or hand over a message; they never send anything themselves.
Here each message is checked against who may see it (their role and, for a Manager, their area), their
own choices for that kind of message (in the app, by email), the business's quiet hours, and how
serious it is. Security alerts are the only ones that cannot be turned off, and they ignore quiet
hours. Emails that go out together to one person become a single summary.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.alerts import rules
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.tenant import tenant_scope
from app.integrations.base import utcnow
from app.models.alerts import Alert, Notification
from app.models.identity import OrganizationUser, Role, User
from app.schemas.alerts import InboxOut, NotificationOut
from app.services import settings as settings_service
from app.services import system_events
from app.services.email import EmailMessage, EmailSender, get_email_sender
from app.services.organizations import _membership_query

logger = logging.getLogger("vyterlix.notifications")


@dataclass
class Member:
    user_id: uuid.UUID
    role: str
    remit: list[str] | None


def members(db: Session) -> list[Member]:
    """The people in the business (the session is scoped to it) who could be told something."""
    rows = db.execute(
        select(OrganizationUser.user_id, Role.code, OrganizationUser.scope)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.status == "active")
        .order_by(OrganizationUser.created_at)
    ).all()
    return [Member(uid, role, (scope or {}).get("kpi_categories")) for uid, role, scope in rows]


def link_url(organization_id: uuid.UUID, link: str | None) -> str | None:
    """A screen of the app, for use in an email (only ever one of our own pages)."""
    if not link or urlsplit(link).netloc:
        return None
    page, _, anchor = link.partition("#")
    url = f"{get_settings().frontend_base_url}/{page}?org={organization_id}"
    return url + (f"#{anchor}" if anchor else "")


# --- making notifications ---------------------------------------------------------------------------


def _preference(db: Session, cache: dict, user_id: uuid.UUID, category: str):
    if user_id not in cache:
        cache[user_id] = {
            p.category: p for p in settings_service.effective_preferences(db, user_id)
        }
    return cache[user_id][category]


def _make(
    db: Session,
    organization_id,
    user_id,
    *,
    category,
    severity,
    title,
    body,
    link,
    alert_id,
    now,
    quiet,
    cache,
) -> Notification | None:
    pref = _preference(db, cache, user_id, category)
    in_app = True if category == rules.SECURITY else pref.in_app
    decision, when = rules.email_decision(
        category=category, severity=severity, wants_email=pref.email, now=now, quiet=quiet
    )
    if not in_app and decision == "none":
        return None  # they have switched this kind off everywhere
    row = Notification(
        organization_id=organization_id, user_id=user_id, alert_id=alert_id, category=category,
        severity=severity, title=title, body=body, link=link, in_app=in_app,
        email_status="none" if decision == "none" else "pending",
        email_after=None if decision == "none" else (when or now), created_at=now,
    )  # fmt: skip
    db.add(row)
    return row


def deliver(
    db: Session,
    alert: Alert,
    *,
    sender: EmailSender | None = None,
    now: datetime | None = None,
    send: bool = True,
) -> list[Notification]:
    """Tell everyone who should hear about a new alert. Emails that are due go out straight away,
    unless the caller is delivering a whole round and will send them together at the end."""
    now = now or utcnow()
    quiet = settings_service.quiet_hours(db)
    cache: dict = {}
    made = []
    for member in members(db):
        if not rules.in_audience(member.role, member.remit, alert.kpi_category):
            continue
        row = _make(
            db, alert.organization_id, member.user_id, category=alert.category, severity=alert.severity,
            title=alert.title, body=alert.body, link=alert.link, alert_id=alert.id, now=now, quiet=quiet, cache=cache,
        )  # fmt: skip
        if row is not None:
            made.append(row)
    db.flush()
    if send:
        send_due_emails(db, sender=sender, now=now)
    return made


def notify_user(
    db: Session, organization_id: uuid.UUID, user_id: uuid.UUID | None, *, category: str, severity: str,
    title: str, body: str, link: str | None = None, sender: EmailSender | None = None, now: datetime | None = None,
) -> Notification | None:  # fmt: skip
    """A message for one person that is not an alert (a follow-up that is due, a result)."""
    if user_id is None:
        return None
    now = now or utcnow()
    row = _make(
        db, organization_id, user_id, category=category, severity=severity, title=title, body=body,
        link=link, alert_id=None, now=now, quiet=settings_service.quiet_hours(db), cache={},
    )  # fmt: skip
    db.flush()
    send_due_emails(db, sender=sender, now=now)
    return row


def record_security(
    db: Session, user: User, title: str, body: str, *, now: datetime | None = None
) -> int:
    """Put a security message in the app inbox of the person it is about, in every business they
    belong to. (The email for it is sent by the account code itself.) Returns how many were made."""
    now = now or utcnow()
    made = 0
    for org, _role in db.execute(_membership_query(user.id)).all():
        with tenant_scope(db, org.id):
            db.add(
                Notification(
                    organization_id=org.id, user_id=user.id, category=rules.SECURITY, severity="high",
                    title=title, body=body, link=None, in_app=True, email_status="sent",
                    email_sent_at=now, created_at=now,
                )
            )  # fmt: skip
            made += 1
    db.flush()
    return made


# --- email ------------------------------------------------------------------------------------------------


def _body_of(rows: list[Notification]) -> str:
    lines = []
    for n in rows:
        lines.append(f"- {n.title}: {n.body}")
        url = link_url(n.organization_id, n.link)
        if url:
            lines.append(f"  {url}")
    return "\n".join(lines)


def send_due_emails(
    db: Session, *, sender: EmailSender | None = None, now: datetime | None = None
) -> int:
    """Send the emails that are due (not waiting for quiet hours to end). Several for one person
    become one summary. Returns how many notifications were emailed."""
    now = now or utcnow()
    due = db.scalars(
        select(Notification)
        .where(Notification.email_status == "pending", Notification.email_after <= now)
        .order_by(Notification.created_at)
    ).all()
    by_user: dict[uuid.UUID, list[Notification]] = {}
    for n in due:
        by_user.setdefault(n.user_id, []).append(n)
    sent = 0
    for user_id, rows in by_user.items():
        user = db.get(User, user_id)
        if user is None:
            continue
        if len(rows) >= rules.DIGEST_AFTER:
            subject = f"[Vyterlix] {len(rows)} things need your attention"
            body = f"Hi {user.full_name},\n\nHere is what needs your attention:\n\n{_body_of(rows)}"
        else:
            subject = f"[Vyterlix] {rows[0].title}"
            body = f"Hi {user.full_name},\n\n{_body_of(rows)}"
        try:
            (get_email_sender() if sender is None else sender).send(
                EmailMessage(to=user.email, subject=subject, body=body)
            )
            status = "sent"
        except Exception:
            logger.error("Could not send a notification email", exc_info=True)
            status = "failed"
            system_events.record(
                db, "email_failed", "An email could not be sent",
                organization_id=rows[0].organization_id, details={"notifications": len(rows)},
            )  # fmt: skip
        for n in rows:
            n.email_status, n.email_sent_at = status, utcnow()
        sent += len(rows) if status == "sent" else 0
    db.flush()
    return sent


# --- the inbox ----------------------------------------------------------------------------------------------


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id, alert_id=n.alert_id, category=n.category, severity=n.severity, title=n.title, body=n.body,
        link=n.link, read=n.read_at is not None, email=n.email_status, created_at=n.created_at,
    )  # fmt: skip


def inbox(db: Session, tenant, *, unread_only: bool = False, limit: int = 50) -> InboxOut:
    mine = (Notification.user_id == tenant.user.id, Notification.in_app.is_(True))
    query = select(Notification).where(*mine).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(*mine, Notification.read_at.is_(None))
    )
    return InboxOut(unread=unread or 0, items=[_out(n) for n in db.scalars(query)])


def mark_read(db: Session, tenant, notification_id: uuid.UUID) -> None:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != tenant.user.id:
        raise NotFoundError("That notification was not found", code="notification_not_found")
    if n.read_at is None:
        n.read_at = utcnow()
    db.commit()


def mark_all_read(db: Session, tenant) -> int:
    result = db.execute(
        update(Notification)
        .where(
            Notification.user_id == tenant.user.id,
            Notification.in_app.is_(True),
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    db.commit()
    return result.rowcount
