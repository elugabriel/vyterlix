# ruff: noqa: E501
"""Reports (Phase 15): writing a report for a person, keeping the copy, sending it on a schedule.

A report is written from results already worked out, stored whole (so it never changes afterwards),
and its PDF and CSV are made from that stored copy. A copy belongs to the person it was written for.
A schedule writes a copy for each person chosen and emails them a link to it: reports are never
attached to an email, so they can only be opened by someone who is logged in.
"""

import logging
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.integrations.base import utcnow
from app.models.identity import Organization, OrganizationUser, User
from app.models.reports import Report, ReportRun, ReportSchedule
from app.reports import render, rules
from app.schemas.reports import (
    MonthsIn,
    ReportOut,
    RunOut,
    RunSummaryOut,
    ScheduleIn,
    ScheduleOut,
    SchedulePatch,
)
from app.services import billing, notifications
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.email import EmailMessage, EmailSender, get_email_sender
from app.services.report_builders import build

logger = logging.getLogger("vyterlix.reports")

DESCRIPTION = {
    "monthly": "One page for the month: how you are doing, your key figures, what changed, your actions and what you learned.",
    "health": "Your business health: the score, each area of the business, how it has moved and what is holding it back.",
    "kpi": "Every key figure, month by month, with how the latest month moved.",
    "outcomes": "What you took up, what came of it, what you learned, and how each kind of action has worked for you.",
}


# --- the reports a business has -------------------------------------------------------------------------


def ensure_standard(db: Session, tenant) -> dict[str, Report]:
    """The four standard reports (made the first time they are wanted)."""
    have = {r.kind: r for r in db.scalars(select(Report))}
    for kind, name, months in rules.STANDARD:
        if kind not in have:
            have[kind] = Report(
                organization_id=tenant.organization_id, kind=kind, name=name, months=months
            )
            db.add(have[kind])
    db.flush()
    return have


def _get(db: Session, report_id: uuid.UUID) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise NotFoundError("That report was not found", code="report_not_found")
    return report


def _run_summary(r: ReportRun) -> RunSummaryOut:
    return RunSummaryOut(
        id=r.id,
        report_id=r.report_id,
        kind=r.kind,
        title=r.title,
        trigger=r.trigger,
        period_start=r.period_start,
        period_end=r.period_end,
        generated_at=r.generated_at,
        emailed=r.emailed_at is not None,
    )


def _names(db: Session, ids) -> dict:
    ids = {uuid.UUID(str(i)) for i in ids}
    return (
        {i: n for i, n in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))}
        if ids
        else {}
    )


def _schedule_out(db: Session, s: ReportSchedule, report: Report) -> ScheduleOut:
    names = _names(db, s.recipients)
    ids = [uuid.UUID(str(i)) for i in s.recipients]
    return ScheduleOut(
        id=s.id, report_id=s.report_id, report_name=report.name, frequency=s.frequency, weekday=s.weekday,
        day_of_month=s.day_of_month, when=rules.describe(s.frequency, s.weekday, s.day_of_month),
        recipients=[names[i] for i in ids if i in names], recipient_ids=[i for i in ids if i in names],
        enabled=s.enabled, next_run_at=s.next_run_at, last_run_at=s.last_run_at,
    )  # fmt: skip


def list_reports(db: Session, tenant) -> list[ReportOut]:
    reports = ensure_standard(db, tenant)
    db.commit()
    schedules = db.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at)).all()
    mine = {
        r.report_id: r
        for r in db.scalars(
            select(ReportRun)
            .where(ReportRun.user_id == tenant.user.id)
            .order_by(ReportRun.generated_at)
        )
    }  # the latest of each: later ones overwrite earlier
    out = []
    for kind, _, _ in rules.STANDARD:
        r = reports[kind]
        out.append(
            ReportOut(
                id=r.id, kind=kind, name=r.name, description=DESCRIPTION[kind], months=1 if kind == "monthly" else r.months,
                fixed_months=kind == "monthly", last_run=_run_summary(mine[r.id]) if r.id in mine else None,
                schedules=[_schedule_out(db, s, r) for s in schedules if s.report_id == r.id],
            )
        )  # fmt: skip
    return out


def set_months(db: Session, tenant, report_id: uuid.UUID, body: MonthsIn) -> ReportOut:
    report = _get(db, report_id)
    if report.kind == "monthly":
        raise AppError(
            "The monthly report always covers one month.", code="months_fixed", status_code=422
        )
    report.months, report.updated_by_user_id = body.months, tenant.user.id
    db.commit()
    return next(r for r in list_reports(db, tenant) if r.id == report.id)


# --- writing one ------------------------------------------------------------------------------------------------


def generate(
    db: Session,
    organization_id: uuid.UUID,
    report: Report,
    *,
    for_user: uuid.UUID,
    trigger: str,
    by_user: uuid.UUID | None,
    schedule_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> ReportRun:
    now = now or utcnow()
    business = db.get(Organization, organization_id).name
    content = build(
        db, kind=report.kind, title=report.name, business=business, months=report.months, now=now
    )
    run = ReportRun(
        organization_id=organization_id, report_id=report.id, user_id=for_user, schedule_id=schedule_id, kind=report.kind,
        title=report.name, trigger=trigger, period_start=date.fromisoformat(content["period_start"]),
        period_end=date.fromisoformat(content["period_end"]), content=content, rules_version=rules.RULES_VERSION,
        generated_by_user_id=by_user, generated_at=now,
    )  # fmt: skip
    db.add(run)
    db.flush()
    return run


def generate_now(db: Session, tenant, report_id: uuid.UUID, meta: RequestMeta) -> RunOut:
    report = _get(db, report_id)
    run = generate(
        db,
        tenant.organization_id,
        report,
        for_user=tenant.user.id,
        trigger="manual",
        by_user=tenant.user.id,
    )
    record_audit(
        db, AuditAction.REPORT_GENERATED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="report", target_id=report.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"kind": report.kind, "run": str(run.id)},
    )  # fmt: skip
    db.commit()
    return _run_out(run)


# --- reading what was written ---------------------------------------------------------------------------------------


def _run_out(r: ReportRun) -> RunOut:
    return RunOut(**_run_summary(r).model_dump(), content=r.content, rules_version=r.rules_version)


def _mine(db: Session, tenant, run_id: uuid.UUID) -> ReportRun:
    """A copy is for the person it was written for; to anyone else it does not exist."""
    run = db.get(ReportRun, run_id)
    if run is None or run.user_id != tenant.user.id:
        raise NotFoundError("That report was not found", code="report_run_not_found")
    return run


def list_runs(
    db: Session, tenant, report_id: uuid.UUID | None = None, limit: int = 30
) -> list[RunSummaryOut]:
    query = (
        select(ReportRun)
        .where(ReportRun.user_id == tenant.user.id)
        .order_by(ReportRun.generated_at.desc())
        .limit(limit)
    )
    if report_id is not None:
        query = query.where(ReportRun.report_id == report_id)
    return [_run_summary(r) for r in db.scalars(query)]


def get_run(db: Session, tenant, run_id: uuid.UUID) -> RunOut:
    return _run_out(_mine(db, tenant, run_id))


def export(
    db: Session, tenant, run_id: uuid.UUID, fmt: str, meta: RequestMeta
) -> tuple[bytes, str, str]:
    """(the file, its name, its type) for a copy, made from the stored content."""
    run = _mine(db, tenant, run_id)
    if fmt == "pdf":
        data, ctype = render.to_pdf(run.content), "application/pdf"
    else:
        data, ctype = rules.to_csv(run.content).encode("utf-8"), "text/csv; charset=utf-8"
    record_audit(
        db, AuditAction.REPORT_EXPORTED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="report_run", target_id=run.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"format": fmt, "kind": run.kind},
    )  # fmt: skip
    db.commit()
    return data, rules.filename(run.kind, run.content, fmt), ctype


# --- schedules ----------------------------------------------------------------------------------------------------------


def _members(db: Session) -> set[uuid.UUID]:
    return set(
        db.scalars(select(OrganizationUser.user_id).where(OrganizationUser.status == "active"))
    )


def _check_when(frequency: str, weekday, day) -> None:
    if frequency == "weekly" and (weekday is None or day is not None):
        raise AppError(
            "A weekly report needs a day of the week, and no day of the month.",
            code="bad_schedule",
            status_code=422,
        )
    if frequency == "monthly" and (day is None or weekday is not None):
        raise AppError(
            "A monthly report needs a day of the month (1 to 28), and no day of the week.",
            code="bad_schedule",
            status_code=422,
        )


def _check_recipients(db: Session, recipients) -> list[str]:
    ids = {uuid.UUID(str(i)) for i in recipients}
    unknown = ids - _members(db)
    if unknown:
        raise AppError(
            "Reports can only be sent to people in this business.",
            code="not_a_member",
            status_code=422,
        )
    return sorted(str(i) for i in ids)


def list_schedules(db: Session) -> list[ScheduleOut]:
    reports = {r.id: r for r in db.scalars(select(Report))}
    return [
        _schedule_out(db, s, reports[s.report_id])
        for s in db.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at))
    ]


def create_schedule(
    db: Session,
    tenant,
    report_id: uuid.UUID,
    body: ScheduleIn,
    meta: RequestMeta,
    now: datetime | None = None,
) -> ScheduleOut:
    report = _get(db, report_id)
    billing.check(db, tenant.organization_id, "scheduled_reports")
    _check_when(body.frequency, body.weekday, body.day_of_month)
    recipients = _check_recipients(db, body.recipients)
    schedule = ReportSchedule(
        organization_id=tenant.organization_id, report_id=report.id, frequency=body.frequency, weekday=body.weekday,
        day_of_month=body.day_of_month, recipients=recipients, enabled=body.enabled,
        next_run_at=rules.next_run(body.frequency, body.weekday, body.day_of_month, now or utcnow()), created_by_user_id=tenant.user.id,
    )  # fmt: skip
    db.add(schedule)
    db.flush()
    record_audit(
        db, AuditAction.REPORT_SCHEDULE_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="report_schedule", target_id=schedule.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"change": "created", "report": report.kind, "when": rules.describe(body.frequency, body.weekday, body.day_of_month), "recipients": len(recipients)},
    )  # fmt: skip
    db.commit()
    return _schedule_out(db, schedule, report)


def _get_schedule(db: Session, schedule_id: uuid.UUID) -> ReportSchedule:
    s = db.get(ReportSchedule, schedule_id)
    if s is None:
        raise NotFoundError("That schedule was not found", code="schedule_not_found")
    return s


def update_schedule(
    db: Session,
    tenant,
    schedule_id: uuid.UUID,
    body: SchedulePatch,
    meta: RequestMeta,
    now: datetime | None = None,
) -> ScheduleOut:
    s = _get_schedule(db, schedule_id)
    sent = body.model_fields_set
    frequency = body.frequency or s.frequency
    weekday = (
        body.weekday if "weekday" in sent else (None if frequency != s.frequency else s.weekday)
    )
    day = (
        body.day_of_month
        if "day_of_month" in sent
        else (None if frequency != s.frequency else s.day_of_month)
    )
    _check_when(frequency, weekday, day)
    if body.recipients is not None:
        s.recipients = _check_recipients(db, body.recipients)
    if body.enabled is not None:
        s.enabled = body.enabled
    when_changed = (frequency, weekday, day) != (s.frequency, s.weekday, s.day_of_month)
    s.frequency, s.weekday, s.day_of_month = frequency, weekday, day
    if when_changed or body.enabled is True:
        s.next_run_at = rules.next_run(frequency, weekday, day, now or utcnow())
    record_audit(
        db, AuditAction.REPORT_SCHEDULE_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="report_schedule", target_id=s.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"change": "updated", "fields": sorted(sent)},
    )  # fmt: skip
    db.commit()
    return _schedule_out(db, s, _get(db, s.report_id))


def delete_schedule(db: Session, tenant, schedule_id: uuid.UUID, meta: RequestMeta) -> None:
    s = _get_schedule(db, schedule_id)
    record_audit(
        db, AuditAction.REPORT_SCHEDULE_CHANGED, actor_user_id=tenant.user.id, organization_id=tenant.organization_id,
        target_type="report_schedule", target_id=s.id, ip_address=meta.ip_address, user_agent=meta.user_agent, details={"change": "deleted"},
    )  # fmt: skip
    db.delete(s)
    db.commit()


# --- delivering what is due ---------------------------------------------------------------------------------------------------


def _link(organization_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return f"{get_settings().frontend_base_url}/reports.html?org={organization_id}#{run_id}"


def run_due(
    db: Session,
    organization_id: uuid.UUID,
    *,
    now: datetime | None = None,
    sender: EmailSender | None = None,
) -> int:
    """Write and email every schedule that has come due. A copy is written for each person chosen who
    is still in the business; the email says what it is and links to it (nothing is attached). Safe to
    run as often as you like: a schedule moves on to its next time as soon as it has been done."""
    now = now or utcnow()
    done = 0
    due = db.scalars(
        select(ReportSchedule).where(
            ReportSchedule.enabled.is_(True), ReportSchedule.next_run_at <= now
        )
    ).all()
    members = _members(db)
    for s in due:
        report = db.get(Report, s.report_id)
        for raw in s.recipients:
            uid = uuid.UUID(str(raw))
            user = db.get(User, uid)
            if uid not in members or user is None:
                continue  # they have left the business
            run = generate(
                db,
                organization_id,
                report,
                for_user=uid,
                trigger="scheduled",
                by_user=None,
                schedule_id=s.id,
                now=now,
            )
            try:
                (get_email_sender() if sender is None else sender).send(
                    EmailMessage(
                        to=user.email,
                        subject=f"[Vyterlix] {report.name}: {rules.period_label(run.period_start, run.period_end)}",
                        body=f"Hi {user.full_name},\n\nYour {report.name.lower()} for {rules.period_label(run.period_start, run.period_end)} is ready.\n\nOpen it, or download it as a PDF or spreadsheet, here (you will need to log in):\n{_link(organization_id, run.id)}\n",
                    )
                )
                run.emailed_at = utcnow()
            except Exception:
                logger.error("Could not email a report", exc_info=True)
            notifications.notify_user(
                db,
                organization_id,
                uid,
                category="data",
                severity="info",
                title=f"{report.name} is ready",
                body=f"Your {report.name.lower()} for {rules.period_label(run.period_start, run.period_end)} has been written.",
                link="reports.html",
                sender=sender,
                now=now,
            )
            done += 1
        s.last_run_at = now
        s.next_run_at = rules.next_run(s.frequency, s.weekday, s.day_of_month, now)
    db.flush()
    return done
