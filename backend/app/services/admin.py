# ruff: noqa: E501
"""Looking after the platform (Phase 17): the businesses, the people, the plans and the audit trail,
for platform staff only.

What staff can see is deliberately narrow: accounts and plans, never the figures inside a business
(sales, costs, customers, reports). Every look at a business or a person, and every change, is
written to the audit log with the reason given. Support staff can look; only admins can change.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func, select, text, tuple_, update
from sqlalchemy.orm import Session, aliased

from app.billing import rules as billing_rules
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.integrations.base import utcnow
from app.models.admin import (
    AdminNote,
    FeatureFlag,
    PlatformStaff,
    SupportCase,
    SystemEvent,
)
from app.models.alerts import Notification
from app.models.billing import Plan, Subscription
from app.models.identity import (
    AuditLog,
    Organization,
    OrganizationUser,
    Role,
    User,
    UserSession,
)
from app.models.integrations import Integration
from app.models.jobs import Job
from app.schemas.admin import (
    AdminAuditPage,
    AdminEntry,
    AdminMember,
    AdminMembership,
    AdminNoteOut,
    AdminOrgDetail,
    AdminOrgPage,
    AdminOrgRow,
    AdminPlan,
    AdminPlanFeature,
    AdminSubscription,
    AdminUsage,
    AdminUserDetail,
    AdminUserPage,
    AdminUserRow,
    CaseDetail,
    CasePage,
    CaseRow,
    FlagOut,
    FlagOverride,
    HealthAccounts,
    HealthEmails,
    HealthIntegrations,
    HealthJobs,
    HealthOut,
    SystemEventOut,
    SystemEventPage,
)
from app.services import billing
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.organizations import update_member

ANYWHERE = ACROSS_TENANTS


@dataclass(frozen=True)
class Staff:
    user: User
    role: str  # support or admin

    @property
    def can_change(self) -> bool:
        return self.role == "admin"


def staff_of(db: Session, user: User) -> Staff | None:
    row = db.scalars(
        select(PlatformStaff).where(
            PlatformStaff.user_id == user.id, PlatformStaff.is_active.is_(True)
        )
    ).first()
    return Staff(user, row.role) if row else None


def need_admin(staff: Staff) -> None:
    if not staff.can_change:
        raise PermissionDeniedError("Only an admin can make this change.", code="admin_only")


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _audit(db: Session, staff: Staff, action: AuditAction, meta: RequestMeta, **kw) -> None:
    record_audit(
        db,
        action,
        actor_user_id=staff.user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        **kw,
    )


def _org(db: Session, org_id: uuid.UUID) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFoundError("Business not found", code="organization_not_found")
    return org


def _user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("Person not found", code="user_not_found")
    return user


# --- businesses ------------------------------------------------------------------------------------


def list_organizations(
    db: Session, *, q: str | None, status: str | None, limit: int, offset: int
) -> AdminOrgPage:
    members = (
        select(OrganizationUser.organization_id.label("org"), func.count().label("n"))
        .where(OrganizationUser.status == "active")
        .group_by(OrganizationUser.organization_id)
        .subquery()
    )
    where = []
    if q:
        where.append(Organization.name.ilike(_like(q), escape="\\"))
    if status:
        where.append(Organization.status == status)
    total = db.scalar(select(func.count()).select_from(Organization).where(*where)) or 0
    rows = db.execute(
        select(Organization, func.coalesce(members.c.n, 0), Subscription, Plan)
        .outerjoin(members, members.c.org == Organization.id)
        .outerjoin(Subscription, Subscription.organization_id == Organization.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .where(*where)
        .order_by(Organization.name, Organization.id)
        .limit(limit)
        .offset(offset)
        .execution_options(**ANYWHERE)
    ).all()
    now = utcnow()
    return AdminOrgPage(
        total=total,
        items=[
            AdminOrgRow(
                id=org.id,
                name=org.name,
                status=org.status,
                created_at=org.created_at,
                member_count=n,
                plan_name=plan.name if plan else None,
                plan_status=_effective(sub, now) if sub else None,
            )  # fmt: skip
            for org, n, sub, plan in rows
        ],
    )


def _effective(sub: Subscription, now: datetime) -> str:
    return billing_rules.effective_status(
        sub.status,
        trial_ends_at=sub.trial_ends_at,
        period_end_at=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        now=now,
    )


def organization_detail(
    db: Session, staff: Staff, org_id: uuid.UUID, meta: RequestMeta
) -> AdminOrgDetail:
    org = _org(db, org_id)
    now = utcnow()
    member_rows = db.execute(
        select(OrganizationUser, User, Role.code)
        .join(User, User.id == OrganizationUser.user_id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.organization_id == org_id)
        .order_by(User.full_name, User.id)
        .execution_options(**ANYWHERE)
    ).all()
    sub = db.scalars(
        select(Subscription)
        .where(Subscription.organization_id == org_id)
        .execution_options(**ANYWHERE)
    ).first()
    plan = db.get(Plan, sub.plan_id) if sub else None
    with tenant_scope(db, org_id):
        usage = AdminUsage(
            members=billing.used(db, "members", now),
            integrations=billing.used(db, "integrations", now),
            scheduled_reports=billing.used(db, "scheduled_reports", now),
        )
    creator = db.get(User, org.created_by_user_id) if org.created_by_user_id else None
    last = db.scalar(
        select(func.max(AuditLog.created_at)).where(AuditLog.organization_id == org_id)
    )
    out = AdminOrgDetail(
        id=org.id, name=org.name, status=org.status, created_at=org.created_at,
        created_by_email=creator.email if creator else None,
        members=[
            AdminMember(
                user_id=u.id, email=u.email, full_name=u.full_name, role=role, status=m.status,
                last_login_at=u.last_login_at, user_active=u.is_active,
            )
            for m, u, role in member_rows
        ],
        subscription=AdminSubscription(
            plan_code=plan.code, plan_name=plan.name, status=_effective(sub, now), interval=sub.interval,
            provider=sub.provider, trial_ends_at=sub.trial_ends_at, current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
        ) if sub and plan else None,
        usage=usage, last_activity_at=last,
        open_cases=db.scalar(select(func.count()).select_from(SupportCase).where(SupportCase.organization_id == org_id, SupportCase.status != "resolved")) or 0,
        notes=_notes(db, AdminNote.organization_id == org_id),
    )  # fmt: skip
    _audit(db, staff, AuditAction.ADMIN_ORGANIZATION_VIEWED, meta, organization_id=org_id, target_type="organization", target_id=org_id)  # fmt: skip
    db.commit()
    return out


def suspend_organization(
    db: Session, staff: Staff, org_id: uuid.UUID, reason: str, meta: RequestMeta
) -> None:
    need_admin(staff)
    org = _org(db, org_id)
    if org.status != "active":
        raise ConflictError("That business is not active.", code="not_active")
    org.status = "suspended"
    _audit(db, staff, AuditAction.ADMIN_ORGANIZATION_SUSPENDED, meta, organization_id=org_id, target_type="organization", target_id=org_id, details={"reason": reason})  # fmt: skip
    db.commit()


def reactivate_organization(
    db: Session, staff: Staff, org_id: uuid.UUID, reason: str, meta: RequestMeta
) -> None:
    need_admin(staff)
    org = _org(db, org_id)
    if org.status != "suspended":
        raise ConflictError("That business is not suspended.", code="not_suspended")
    org.status = "active"
    _audit(db, staff, AuditAction.ADMIN_ORGANIZATION_REACTIVATED, meta, organization_id=org_id, target_type="organization", target_id=org_id, details={"reason": reason})  # fmt: skip
    db.commit()


def change_member_role(
    db: Session,
    staff: Staff,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    reason: str,
    meta: RequestMeta,
) -> None:
    """Change someone's role in a business (for example when its only owner has left). Goes through
    the same rules as the business's own owner would, including always keeping an owner."""
    need_admin(staff)
    _org(db, org_id)
    with tenant_scope(db, org_id):
        update_member(db, org_id, staff.user, user_id, {"role": role}, meta)
    _audit(db, staff, AuditAction.ADMIN_MEMBER_ROLE_CHANGED, meta, organization_id=org_id, target_type="user", target_id=user_id, details={"role": role, "reason": reason})  # fmt: skip
    db.commit()


def extend_trial(
    db: Session, staff: Staff, org_id: uuid.UUID, days: int, reason: str, meta: RequestMeta
) -> AdminSubscription:
    need_admin(staff)
    _org(db, org_id)
    now = utcnow()
    with tenant_scope(db, org_id):
        sub = billing.extend_trial(db, org_id, days, now)
        plan = db.get(Plan, sub.plan_id)
        out = AdminSubscription(
            plan_code=plan.code, plan_name=plan.name, status=_effective(sub, now), interval=sub.interval,
            provider=sub.provider, trial_ends_at=sub.trial_ends_at, current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
        )  # fmt: skip
        _audit(db, staff, AuditAction.ADMIN_TRIAL_EXTENDED, meta, organization_id=org_id, target_type="subscription", target_id=sub.id, details={"days": days, "reason": reason})  # fmt: skip
        db.commit()
    return out


# --- people ----------------------------------------------------------------------------------------


def _user_row(user: User, count: int, staff_role: str | None) -> AdminUserRow:
    return AdminUserRow(
        id=user.id, email=user.email, full_name=user.full_name, is_active=user.is_active,
        email_verified=user.email_verified_at is not None, last_login_at=user.last_login_at,
        created_at=user.created_at, organization_count=count, staff_role=staff_role,
    )  # fmt: skip


def list_users(db: Session, *, q: str | None, limit: int, offset: int) -> AdminUserPage:
    counts = (
        select(OrganizationUser.user_id.label("uid"), func.count().label("n"))
        .group_by(OrganizationUser.user_id)
        .subquery()
    )
    where = []
    if q:
        where.append(
            User.email.ilike(_like(q.lower()), escape="\\")
            | User.full_name.ilike(_like(q), escape="\\")
        )
    total = db.scalar(select(func.count()).select_from(User).where(*where)) or 0
    rows = db.execute(
        select(User, func.coalesce(counts.c.n, 0), PlatformStaff.role)
        .outerjoin(counts, counts.c.uid == User.id)
        .outerjoin(
            PlatformStaff,
            (PlatformStaff.user_id == User.id) & PlatformStaff.is_active.is_(True),
        )
        .where(*where)
        .order_by(User.email, User.id)
        .limit(limit)
        .offset(offset)
        .execution_options(**ANYWHERE)
    ).all()
    return AdminUserPage(total=total, items=[_user_row(u, n, r) for u, n, r in rows])


def user_detail(
    db: Session, staff: Staff, user_id: uuid.UUID, meta: RequestMeta
) -> AdminUserDetail:
    user = _user(db, user_id)
    memberships = db.execute(
        select(OrganizationUser, Organization, Role.code)
        .join(Organization, Organization.id == OrganizationUser.organization_id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.user_id == user_id)
        .order_by(Organization.name)
        .execution_options(**ANYWHERE)
    ).all()
    sessions = db.scalar(
        select(func.count())
        .select_from(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > func.now(),
        )
    )
    row = db.scalars(
        select(PlatformStaff).where(
            PlatformStaff.user_id == user_id, PlatformStaff.is_active.is_(True)
        )
    ).first()
    out = AdminUserDetail(
        **_user_row(user, len(memberships), row.role if row else None).model_dump(),
        memberships=[
            AdminMembership(
                organization_id=o.id, organization_name=o.name, organization_status=o.status,
                role=role, status=m.status,
            )
            for m, o, role in memberships
        ],
        active_sessions=sessions or 0,
        notes=_notes(db, AdminNote.user_id == user_id),
    )  # fmt: skip
    _audit(db, staff, AuditAction.ADMIN_USER_VIEWED, meta, target_type="user", target_id=user_id)
    db.commit()
    return out


def _is_staff(db: Session, user_id: uuid.UUID) -> bool:
    return (
        db.scalars(
            select(PlatformStaff.id).where(
                PlatformStaff.user_id == user_id, PlatformStaff.is_active.is_(True)
            )
        ).first()
        is not None
    )


def disable_user(
    db: Session, staff: Staff, user_id: uuid.UUID, reason: str, meta: RequestMeta
) -> None:
    """Lock an account and end every session it has. Staff accounts cannot be locked this way (their
    staff rights must be taken away first), which also stops anyone locking themselves out."""
    need_admin(staff)
    user = _user(db, user_id)
    if _is_staff(db, user_id):
        raise ConflictError(
            "That person is platform staff. Take their staff rights away first.",
            code="staff_account",
        )
    if not user.is_active:
        raise ConflictError("That account is already locked.", code="already_disabled")
    user.is_active = False
    ended = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    ).rowcount
    _audit(db, staff, AuditAction.ADMIN_USER_DISABLED, meta, target_type="user", target_id=user_id, details={"reason": reason, "sessions_ended": ended})  # fmt: skip
    db.commit()


def enable_user(
    db: Session, staff: Staff, user_id: uuid.UUID, reason: str, meta: RequestMeta
) -> None:
    need_admin(staff)
    user = _user(db, user_id)
    if user.is_active:
        raise ConflictError("That account is not locked.", code="not_disabled")
    user.is_active = True
    _audit(db, staff, AuditAction.ADMIN_USER_ENABLED, meta, target_type="user", target_id=user_id, details={"reason": reason})  # fmt: skip
    db.commit()


# --- the audit trail ---------------------------------------------------------------------------------


def audit_trail(
    db: Session,
    *,
    organization_id: uuid.UUID | None,
    actor_email: str | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    before: uuid.UUID | None,
    limit: int,
) -> AdminAuditPage:
    """Everything that was recorded, across every business, newest first."""
    stmt = (
        select(AuditLog, User.email, Organization.name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .outerjoin(Organization, Organization.id == AuditLog.organization_id)
    )
    if organization_id:
        stmt = stmt.where(AuditLog.organization_id == organization_id)
    if actor_email:
        stmt = stmt.where(User.email == actor_email.strip().lower())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    if before:
        cursor = db.get(AuditLog, before)
        if cursor is None:
            raise NotFoundError("Unknown cursor", code="invalid_cursor")
        stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < (cursor.created_at, cursor.id))
    rows = db.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
    ).all()
    more = len(rows) > limit
    entries = [
        AdminEntry(
            id=e.id, action=e.action, created_at=e.created_at, actor_email=email,
            organization_id=e.organization_id, organization_name=org_name, target_type=e.target_type,
            target_id=e.target_id, ip_address=str(e.ip_address) if e.ip_address else None, details=e.details,
        )
        for e, email, org_name in rows[:limit]
    ]  # fmt: skip
    return AdminAuditPage(entries=entries, next_before=entries[-1].id if more else None)


# --- plans -----------------------------------------------------------------------------------------------


def _plan_out(db: Session, plan: Plan) -> AdminPlan:
    rows = billing._rows(db, plan.id)
    return AdminPlan(
        code=plan.code, name=plan.name, price_month_pence=plan.price_month_pence,
        price_year_pence=plan.price_year_pence, currency=plan.currency, self_serve=plan.self_serve,
        is_public=plan.is_public, is_active=plan.is_active, provider_prices=plan.provider_prices or {},
        features=[
            AdminPlanFeature(feature=f, enabled=bool(rows.get(f) and rows[f].enabled), limit=rows[f].limit if rows.get(f) else None)
            for f in billing_rules.FEATURE_NOUN
        ],
    )  # fmt: skip


def list_plans(db: Session) -> list[AdminPlan]:
    return [_plan_out(db, p) for p in db.scalars(select(Plan).order_by(Plan.sort_order))]


def change_plan(
    db: Session, staff: Staff, code: str, changes: dict, reason: str, meta: RequestMeta
) -> AdminPlan:
    """Change a plan's prices or whether it is shown or sold. Nobody already paying is charged
    differently: they pay what they signed up for."""
    need_admin(staff)
    plan = billing.plan_by_code(db, code)
    if plan is None:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    before = {k: getattr(plan, k) for k in changes}
    billing.set_plan(
        db, code, price_month=changes.get("price_month_pence"), price_year=changes.get("price_year_pence"),
        is_public=changes.get("is_public"), is_active=changes.get("is_active"),
    )  # fmt: skip
    _audit(db, staff, AuditAction.ADMIN_PLAN_CHANGED, meta, target_type="plan", target_id=code, details={"from": before, "to": changes, "reason": reason})  # fmt: skip
    db.commit()
    return _plan_out(db, plan)


def change_entitlement(
    db: Session,
    staff: Staff,
    code: str,
    feature: str,
    enabled: bool,
    limit: int | None,
    reason: str,
    meta: RequestMeta,
) -> AdminPlan:
    need_admin(staff)
    plan = billing.plan_by_code(db, code)
    if plan is None:
        raise NotFoundError("That plan was not found", code="plan_not_found")
    old = billing._rows(db, plan.id).get(feature)
    before = {"enabled": old.enabled, "limit": old.limit} if old else None
    billing.set_entitlement(db, code, feature, enabled=enabled, limit=limit)
    _audit(db, staff, AuditAction.ADMIN_PLAN_CHANGED, meta, target_type="plan", target_id=code, details={"feature": feature, "from": before, "to": {"enabled": enabled, "limit": limit}, "reason": reason})  # fmt: skip
    db.commit()
    return _plan_out(db, plan)


# --- who is staff (the command line only) --------------------------------------------------------------------------------------


def grant_staff(db: Session, email: str, role: str) -> PlatformStaff:
    user = db.scalars(select(User).where(User.email == email.strip().lower())).first()
    if user is None:
        raise NotFoundError("No account has that email address.", code="user_not_found")
    row = db.scalars(select(PlatformStaff).where(PlatformStaff.user_id == user.id)).first()
    if row is None:
        row = PlatformStaff(user_id=user.id, role=role)
        db.add(row)
    else:
        row.role, row.is_active = role, True
    record_audit(
        db,
        AuditAction.ADMIN_STAFF_GRANTED,
        target_type="user",
        target_id=user.id,
        details={"role": role},
    )
    db.commit()
    return row


def revoke_staff(db: Session, email: str) -> None:
    user = db.scalars(select(User).where(User.email == email.strip().lower())).first()
    row = (
        db.scalars(select(PlatformStaff).where(PlatformStaff.user_id == user.id)).first()
        if user
        else None
    )
    if row is None or not row.is_active:
        raise NotFoundError("That person is not platform staff.", code="not_staff")
    row.is_active = False
    record_audit(db, AuditAction.ADMIN_STAFF_REVOKED, target_type="user", target_id=user.id)
    db.commit()


def list_staff(db: Session) -> list[tuple[str, str, bool]]:
    return [
        (u.email, s.role, s.is_active)
        for s, u in db.execute(
            select(PlatformStaff, User)
            .join(User, User.id == PlatformStaff.user_id)
            .order_by(User.email)
        )
    ]


# --- notes ---------------------------------------------------------------------------------------------------


def _notes(db: Session, *conditions) -> list[AdminNoteOut]:
    rows = db.execute(
        select(AdminNote, User.email)
        .outerjoin(User, User.id == AdminNote.author_user_id)
        .where(*conditions)
        .order_by(AdminNote.created_at.desc(), AdminNote.id.desc())
    ).all()
    return [
        AdminNoteOut(id=n.id, body=n.body, author_email=email, created_at=n.created_at)
        for n, email in rows
    ]


def add_note(
    db: Session,
    staff: Staff,
    body: str,
    organization_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    meta: RequestMeta,
) -> AdminNoteOut:
    """A note about a business or a person (support and admin may both write them)."""
    if organization_id is None and user_id is None:
        raise AppError(
            "Say which business or person the note is about.",
            code="note_needs_a_subject",
            status_code=422,
        )
    if organization_id is not None:
        _org(db, organization_id)
    if user_id is not None:
        _user(db, user_id)
    note = AdminNote(
        organization_id=organization_id,
        user_id=user_id,
        author_user_id=staff.user.id,
        body=body.strip(),
    )
    db.add(note)
    db.flush()
    out = AdminNoteOut(
        id=note.id, body=note.body, author_email=staff.user.email, created_at=note.created_at
    )
    _audit(db, staff, AuditAction.ADMIN_NOTE_ADDED, meta, organization_id=organization_id, target_type="user" if user_id else "organization", target_id=user_id or organization_id)  # fmt: skip
    db.commit()
    return out


# --- support cases --------------------------------------------------------------------------------------------


def _case_row(case: SupportCase, org_name: str | None, assignee: str | None) -> CaseRow:
    return CaseRow(
        id=case.id, subject=case.subject, status=case.status, priority=case.priority,
        organization_id=case.organization_id, organization_name=org_name, requester_email=case.requester_email,
        assigned_to_email=assignee, created_at=case.created_at, updated_at=case.updated_at, resolved_at=case.resolved_at,
    )  # fmt: skip


def _case_detail(db: Session, case: SupportCase) -> CaseDetail:
    org = db.get(Organization, case.organization_id) if case.organization_id else None
    assignee = db.get(User, case.assigned_to_user_id) if case.assigned_to_user_id else None
    creator = db.get(User, case.created_by_user_id) if case.created_by_user_id else None
    row = _case_row(case, org.name if org else None, assignee.email if assignee else None)
    return CaseDetail(
        **row.model_dump(),
        created_by_email=creator.email if creator else None,
        notes=_notes(db, AdminNote.case_id == case.id),
    )


def _get_case(db: Session, case_id: uuid.UUID) -> SupportCase:
    case = db.get(SupportCase, case_id)
    if case is None:
        raise NotFoundError("Case not found", code="case_not_found")
    return case


def create_case(
    db: Session,
    staff: Staff,
    *,
    subject: str,
    organization_id: uuid.UUID | None,
    requester_email: str | None,
    priority: str,
    note: str | None,
    meta: RequestMeta,
) -> CaseDetail:
    if organization_id is not None:
        _org(db, organization_id)
    now = utcnow()
    case = SupportCase(
        created_at=now, updated_at=now, subject=subject.strip(), organization_id=organization_id, priority=priority, created_by_user_id=staff.user.id,
        requester_email=requester_email.strip().lower() if requester_email else None,
    )  # fmt: skip
    db.add(case)
    db.flush()
    if note:
        db.add(AdminNote(case_id=case.id, author_user_id=staff.user.id, body=note.strip()))
    _audit(db, staff, AuditAction.ADMIN_CASE_CREATED, meta, organization_id=organization_id, target_type="support_case", target_id=case.id, details={"priority": priority})  # fmt: skip
    db.flush()
    out = _case_detail(db, case)
    db.commit()
    return out


def list_cases(
    db: Session,
    *,
    status: str | None,
    assigned_to: uuid.UUID | None,
    organization_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> CasePage:
    where = []
    if status:
        where.append(SupportCase.status == status)
    if assigned_to:
        where.append(SupportCase.assigned_to_user_id == assigned_to)
    if organization_id:
        where.append(SupportCase.organization_id == organization_id)
    total = db.scalar(select(func.count()).select_from(SupportCase).where(*where)) or 0
    assignee = aliased(User)
    rows = db.execute(
        select(SupportCase, Organization.name, assignee.email)
        .outerjoin(Organization, Organization.id == SupportCase.organization_id)
        .outerjoin(assignee, assignee.id == SupportCase.assigned_to_user_id)
        .where(*where)
        .order_by(
            case((SupportCase.status == "resolved", 1), else_=0),
            SupportCase.created_at.desc(),
            SupportCase.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return CasePage(total=total, items=[_case_row(c, org, who) for c, org, who in rows])


def case_detail(db: Session, case_id: uuid.UUID) -> CaseDetail:
    return _case_detail(db, _get_case(db, case_id))


def update_case(
    db: Session, staff: Staff, case_id: uuid.UUID, changes: dict, meta: RequestMeta
) -> CaseDetail:
    case = _get_case(db, case_id)
    done: dict = {}
    if changes.get("subject") is not None and changes["subject"].strip() != case.subject:
        done["subject"] = {"from": case.subject, "to": changes["subject"].strip()}
        case.subject = changes["subject"].strip()
    if changes.get("priority") is not None and changes["priority"] != case.priority:
        done["priority"] = {"from": case.priority, "to": changes["priority"]}
        case.priority = changes["priority"]
    status = changes.get("status")
    if status is not None and status != case.status:
        done["status"] = {"from": case.status, "to": status}
        case.status = status
        case.resolved_at = utcnow() if status == "resolved" else None
    if changes.get("unassign") and case.assigned_to_user_id is not None:
        done["assigned_to"] = {"to": None}
        case.assigned_to_user_id = None
    elif changes.get("assigned_to_email"):
        target = db.scalars(
            select(User).where(User.email == changes["assigned_to_email"].strip().lower())
        ).first()
        if target is None or not _is_staff(db, target.id):
            raise AppError(
                "A case can only be given to a member of staff.", code="not_staff", status_code=422
            )
        if target.id != case.assigned_to_user_id:
            done["assigned_to"] = {"to": target.email}
            case.assigned_to_user_id = target.id
    if done:
        _audit(db, staff, AuditAction.ADMIN_CASE_UPDATED, meta, organization_id=case.organization_id, target_type="support_case", target_id=case.id, details=done)  # fmt: skip
        db.flush()
    out = _case_detail(db, case)
    db.commit()
    return out


def add_case_note(
    db: Session, staff: Staff, case_id: uuid.UUID, body: str, meta: RequestMeta
) -> CaseDetail:
    case = _get_case(db, case_id)
    db.add(AdminNote(case_id=case.id, author_user_id=staff.user.id, body=body.strip()))
    case.updated_at = utcnow()
    _audit(db, staff, AuditAction.ADMIN_NOTE_ADDED, meta, organization_id=case.organization_id, target_type="support_case", target_id=case.id)  # fmt: skip
    db.flush()
    out = _case_detail(db, case)
    db.commit()
    return out


# --- feature flags ------------------------------------------------------------------------------------------------


def _flag_out(db: Session, flag: FeatureFlag) -> FlagOut:
    ids = [uuid.UUID(k) for k in (flag.org_overrides or {})]
    names = (
        {o.id: o.name for o in db.scalars(select(Organization).where(Organization.id.in_(ids)))}
        if ids
        else {}
    )
    return FlagOut(
        key=flag.key, description=flag.description, enabled=flag.enabled,
        overrides=sorted(
            (FlagOverride(organization_id=uuid.UUID(k), organization_name=names.get(uuid.UUID(k)), enabled=v) for k, v in flag.org_overrides.items()),
            key=lambda o: (o.organization_name or "", str(o.organization_id)),
        ),
    )  # fmt: skip


def _get_flag(db: Session, key: str) -> FeatureFlag:
    flag = db.scalars(select(FeatureFlag).where(FeatureFlag.key == key)).first()
    if flag is None:
        raise NotFoundError("That feature switch was not found", code="flag_not_found")
    return flag


def list_flags(db: Session) -> list[FlagOut]:
    return [_flag_out(db, f) for f in db.scalars(select(FeatureFlag).order_by(FeatureFlag.key))]


def _flag_audit(
    db: Session, staff: Staff, meta: RequestMeta, key: str, reason: str, **details
) -> None:
    _audit(db, staff, AuditAction.ADMIN_FLAG_CHANGED, meta, target_type="feature_flag", target_id=key, details={"reason": reason, **details})  # fmt: skip


def create_flag(
    db: Session,
    staff: Staff,
    key: str,
    description: str,
    enabled: bool,
    reason: str,
    meta: RequestMeta,
) -> FlagOut:
    need_admin(staff)
    if db.scalars(select(FeatureFlag.id).where(FeatureFlag.key == key)).first():
        raise ConflictError("There is already a feature switch with that name.", code="flag_exists")
    flag = FeatureFlag(key=key, description=description.strip(), enabled=enabled, org_overrides={})
    db.add(flag)
    db.flush()
    _flag_audit(db, staff, meta, key, reason, what="created", enabled=enabled)
    out = _flag_out(db, flag)
    db.commit()
    return out


def change_flag(
    db: Session, staff: Staff, key: str, changes: dict, reason: str, meta: RequestMeta
) -> FlagOut:
    need_admin(staff)
    flag = _get_flag(db, key)
    done = {}
    if (
        changes.get("description") is not None
        and changes["description"].strip() != flag.description
    ):
        flag.description = changes["description"].strip()
        done["description"] = flag.description
    if changes.get("enabled") is not None and changes["enabled"] != flag.enabled:
        done["enabled"] = {"from": flag.enabled, "to": changes["enabled"]}
        flag.enabled = changes["enabled"]
    if done:
        _flag_audit(db, staff, meta, key, reason, what="changed", **done)
    out = _flag_out(db, flag)
    db.commit()
    return out


def set_flag_override(
    db: Session,
    staff: Staff,
    key: str,
    organization_id: uuid.UUID,
    enabled: bool,
    reason: str,
    meta: RequestMeta,
) -> FlagOut:
    need_admin(staff)
    flag = _get_flag(db, key)
    _org(db, organization_id)
    flag.org_overrides = {**flag.org_overrides, str(organization_id): enabled}
    _flag_audit(
        db,
        staff,
        meta,
        key,
        reason,
        what="override_set",
        organization_id=str(organization_id),
        enabled=enabled,
    )
    out = _flag_out(db, flag)
    db.commit()
    return out


def clear_flag_override(
    db: Session, staff: Staff, key: str, organization_id: uuid.UUID, reason: str, meta: RequestMeta
) -> FlagOut:
    need_admin(staff)
    flag = _get_flag(db, key)
    if str(organization_id) not in flag.org_overrides:
        raise NotFoundError(
            "That business has no entry for this switch.", code="override_not_found"
        )
    flag.org_overrides = {k: v for k, v in flag.org_overrides.items() if k != str(organization_id)}
    _flag_audit(
        db, staff, meta, key, reason, what="override_cleared", organization_id=str(organization_id)
    )
    out = _flag_out(db, flag)
    db.commit()
    return out


def delete_flag(db: Session, staff: Staff, key: str, reason: str, meta: RequestMeta) -> None:
    need_admin(staff)
    flag = _get_flag(db, key)
    db.delete(flag)
    _flag_audit(db, staff, meta, key, reason, what="deleted")
    db.commit()


# --- what has gone wrong, and how the system is --------------------------------------------------------------------------


def list_events(
    db: Session,
    *,
    severity: str | None,
    kind: str | None,
    open_only: bool,
    before: uuid.UUID | None,
    limit: int,
) -> SystemEventPage:
    stmt = select(SystemEvent, Organization.name).outerjoin(
        Organization, Organization.id == SystemEvent.organization_id
    )
    if severity:
        stmt = stmt.where(SystemEvent.severity == severity)
    if kind:
        stmt = stmt.where(SystemEvent.kind == kind)
    if open_only:
        stmt = stmt.where(SystemEvent.resolved_at.is_(None))
    if before:
        cursor = db.get(SystemEvent, before)
        if cursor is None:
            raise NotFoundError("Unknown cursor", code="invalid_cursor")
        stmt = stmt.where(
            tuple_(SystemEvent.created_at, SystemEvent.id) < (cursor.created_at, cursor.id)
        )
    rows = db.execute(
        stmt.order_by(SystemEvent.created_at.desc(), SystemEvent.id.desc()).limit(limit + 1)
    ).all()
    items = [
        SystemEventOut(
            id=e.id, kind=e.kind, severity=e.severity, message=e.message, details=e.details,
            organization_id=e.organization_id, organization_name=name, created_at=e.created_at, resolved_at=e.resolved_at,
        )
        for e, name in rows[:limit]
    ]  # fmt: skip
    return SystemEventPage(items=items, next_before=items[-1].id if len(rows) > limit else None)


def resolve_event(db: Session, staff: Staff, event_id: uuid.UUID, meta: RequestMeta) -> None:
    event = db.get(SystemEvent, event_id)
    if event is None:
        raise NotFoundError("Event not found", code="event_not_found")
    if event.resolved_at is not None:
        raise ConflictError("That has already been dealt with.", code="already_resolved")
    event.resolved_at, event.resolved_by_user_id = utcnow(), staff.user.id
    _audit(db, staff, AuditAction.ADMIN_EVENT_RESOLVED, meta, organization_id=event.organization_id, target_type="system_event", target_id=event.id, details={"kind": event.kind})  # fmt: skip
    db.commit()


STUCK_AFTER = timedelta(minutes=5)  # work "running" that has not been heard from for this long


def _count(db: Session, stmt) -> int:
    return db.scalar(stmt.execution_options(**ANYWHERE)) or 0


def system_health(db: Session, now: datetime | None = None) -> HealthOut:
    now = now or utcnow()
    day_ago = now - timedelta(days=1)
    try:
        db.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False
    try:
        migration = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        db.rollback()
        migration = None
    settings = get_settings()
    queued = _count(db, select(func.count()).select_from(Job).where(Job.status == "queued"))
    running = _count(db, select(func.count()).select_from(Job).where(Job.status == "running"))
    stuck = _count(
        db,
        select(func.count())
        .select_from(Job)
        .where(Job.status == "running", Job.heartbeat_at < now - STUCK_AFTER),
    )
    failed_jobs = _count(
        db,
        select(func.count())
        .select_from(Job)
        .where(Job.status == "failed", Job.finished_at >= day_ago),
    )
    oldest = db.scalar(
        select(func.min(Job.run_after))
        .where(Job.status == "queued", Job.run_after <= now)
        .execution_options(**ANYWHERE)
    )
    waiting_mail = _count(
        db,
        select(func.count())
        .select_from(Notification)
        .where(Notification.email_status == "pending", Notification.email_after <= now),
    )
    failed_mail = _count(
        db,
        select(func.count())
        .select_from(Notification)
        .where(Notification.email_status == "failed", Notification.email_sent_at >= day_ago),
    )
    connected = _count(
        db,
        select(func.count()).select_from(Integration).where(Integration.status != "disconnected"),
    )
    attention = _count(
        db,
        select(func.count()).select_from(Integration).where(
            (Integration.status == "needs_reauth") | ((Integration.status == "connected") & (Integration.consecutive_failures > 0))
        ),
    )  # fmt: skip
    by_org = dict(
        db.execute(select(Organization.status, func.count()).group_by(Organization.status)).all()
    )
    by_sub = dict(
        db.execute(
            select(Subscription.status, func.count())
            .group_by(Subscription.status)
            .execution_options(**ANYWHERE)
        ).all()
    )
    errors = (
        db.scalar(
            select(func.count())
            .select_from(SystemEvent)
            .where(SystemEvent.severity == "error", SystemEvent.resolved_at.is_(None))
        )
        or 0
    )
    warnings = (
        db.scalar(
            select(func.count())
            .select_from(SystemEvent)
            .where(SystemEvent.severity == "warning", SystemEvent.resolved_at.is_(None))
        )
        or 0
    )
    cases = (
        db.scalar(
            select(func.count()).select_from(SupportCase).where(SupportCase.status != "resolved")
        )
        or 0
    )
    return HealthOut(
        status="ok" if database_ok and errors == 0 and stuck == 0 else "attention",
        checked_at=now, database_ok=database_ok, migration=migration, environment=settings.env,
        email_backend=settings.email_backend, ai_provider=settings.ai_provider,
        jobs=HealthJobs(queued=queued, running=running, stuck=stuck, failed_last_day=failed_jobs, oldest_waiting_seconds=int((now - oldest).total_seconds()) if oldest else None),
        emails=HealthEmails(waiting=waiting_mail, failed_last_day=failed_mail),
        integrations=HealthIntegrations(connected=connected, needing_attention=attention),
        accounts=HealthAccounts(
            businesses=by_org, people=db.scalar(select(func.count()).select_from(User)) or 0,
            locked_people=db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(False))) or 0, subscriptions=by_sub,
        ),
        open_errors=errors, open_warnings=warnings, open_cases=cases,
    )  # fmt: skip
