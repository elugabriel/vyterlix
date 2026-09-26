"""The audit trail: who did what, when, from where.

`audit_logs` is append-only, enforced by a database trigger (migration "audit log
append-only"): rows can't be edited or deleted, except that deleting a user or an
organisation blanks the matching id so the history survives. Retention clean-up (hardening
phase L1) must opt in with `SET LOCAL vyterlix.allow_audit_delete = 'on'`.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.identity import AuditLog, User
from app.schemas.audit import AuditActor, AuditEntryOut, AuditLogPage


class AuditAction(StrEnum):
    """Every audited action. Names are `<subject>.<what_happened>`, past tense."""

    # Account
    USER_REGISTERED = "user.registered"
    USER_EMAIL_VERIFIED = "user.email_verified"
    USER_PROFILE_UPDATED = "user.profile_updated"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_PASSWORD_CHANGE_FAILED = "user.password_change_failed"
    # Sign-in
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGIN_BLOCKED = "auth.login_blocked"
    AUTH_LOGOUT = "auth.logout"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    AUTH_PASSWORD_RESET = "auth.password_reset"
    # Organisation
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    MEMBER_UPDATED = "member.updated"
    INVITATION_CREATED = "invitation.created"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_ACCEPT_REJECTED = "invitation.accept_rejected"
    # Business profile
    BUSINESS_PROFILE_CREATED = "business.profile_created"
    BUSINESS_PROFILE_UPDATED = "business.profile_updated"
    GOAL_CREATED = "goal.created"
    GOAL_UPDATED = "goal.updated"
    LIST_ITEM_ADDED = "business.list_item_added"
    LIST_ITEM_UPDATED = "business.list_item_updated"
    SEASON_CREATED = "season.created"
    SEASON_UPDATED = "season.updated"


def record_audit(
    db: Session,
    action: AuditAction,
    *,
    actor_user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the current transaction. Never put secrets in `details`."""
    if not isinstance(action, AuditAction):
        raise TypeError(f"Unknown audit action {action!r}; add it to AuditAction")
    db.add(
        AuditLog(
            action=action.value,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,
            details=details,
        )
    )


def list_audit_log(
    db: Session,
    organization_id: uuid.UUID,
    *,
    limit: int,
    before: uuid.UUID | None = None,
    action: AuditAction | None = None,
) -> AuditLogPage:
    """Newest first. `before` is the id of the last entry of the previous page."""
    # audit_logs isn't a tenant-scoped table (account events have no organisation),
    # so this filter is explicit — and tested.
    stmt = (
        select(AuditLog, User)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.organization_id == organization_id)
    )
    if action is not None:
        stmt = stmt.where(AuditLog.action == action.value)
    if before is not None:
        cursor = db.scalar(
            select(AuditLog).where(
                AuditLog.id == before, AuditLog.organization_id == organization_id
            )
        )
        if cursor is None:
            raise NotFoundError("Unknown cursor", code="invalid_cursor")
        stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < (cursor.created_at, cursor.id))

    rows = db.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    entries = [
        AuditEntryOut(
            id=entry.id,
            action=entry.action,
            actor=AuditActor(id=user.id, full_name=user.full_name, email=user.email)
            if user
            else None,
            target_type=entry.target_type,
            target_id=entry.target_id,
            ip_address=str(entry.ip_address) if entry.ip_address else None,
            details=entry.details,
            created_at=entry.created_at,
        )
        for entry, user in rows
    ]
    return AuditLogPage(entries=entries, next_before=entries[-1].id if has_more else None)
