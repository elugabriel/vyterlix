"""Inviting people into an organisation, and accepting invitations.

Owner-side functions run inside a tenant-scoped session. The invitee side (preview,
accept) starts with no organisation in scope, finds the invitation by its token across
organisations, then scopes itself to that invitation's organisation.
"""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.security import hash_token, new_token
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.models.identity import (
    Organization,
    OrganizationInvitation,
    OrganizationUser,
    Role,
    User,
)
from app.schemas.invitations import InvitationOut, InvitationPreviewOut, InvitationStatus
from app.schemas.organizations import OrganizationOut, Remit
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.email import EmailMessage, EmailSender


def _status(inv: OrganizationInvitation) -> InvitationStatus:
    if inv.accepted_at is not None:
        return "accepted"
    if inv.revoked_at is not None:
        return "revoked"
    if inv.expires_at <= datetime.now(UTC):
        return "expired"
    return "pending"


def _inviter_name(db: Session, inv: OrganizationInvitation) -> str | None:
    inviter = db.get(User, inv.invited_by_user_id) if inv.invited_by_user_id else None
    return inviter.full_name if inviter else None


def _out(db: Session, inv: OrganizationInvitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        role=inv.role.code,
        remit=Remit.model_validate(inv.scope) if inv.scope else None,
        status=_status(inv),
        invited_by=_inviter_name(db, inv),
        created_at=inv.created_at,
        expires_at=inv.expires_at,
    )


def _invalid_invitation() -> AppError:
    # One answer for unknown, expired, revoked and already-used links.
    return AppError("This invitation is invalid or has expired", code="invalid_invitation")


# --- owner side (session scoped to the organisation) ------------------------------------


def create_invitation(
    db: Session,
    org: Organization,
    inviter: User,
    email: str,
    role_code: str,
    remit: dict | None,
    sender: EmailSender,
    meta: RequestMeta,
) -> InvitationOut:
    settings = get_settings()

    already_member = db.scalar(
        select(OrganizationUser.id)
        .join(User, User.id == OrganizationUser.user_id)
        .where(User.email == email, OrganizationUser.status == "active")
    )
    if already_member:
        raise ConflictError("That person is already a member", code="already_member")

    # Re-inviting replaces any open invitation, so only the newest link works.
    db.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.email == email,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )

    role = db.scalars(
        select(Role).where(Role.code == role_code, Role.organization_id.is_(None))
    ).one()
    raw, hashed = new_token()
    inv = OrganizationInvitation(
        email=email,
        role_id=role.id,
        scope=remit,
        token_hash=hashed,
        invited_by_user_id=inviter.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.invitation_ttl_days),
    )
    db.add(inv)
    db.flush()
    record_audit(
        db,
        AuditAction.INVITATION_CREATED,
        actor_user_id=inviter.id,
        organization_id=org.id,
        target_type="invitation",
        target_id=inv.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"email": email, "role": role_code},
    )
    db.commit()
    db.refresh(inv)

    link = f"{settings.frontend_base_url}/accept-invite.html?{urlencode({'token': raw})}"
    sender.send(
        EmailMessage(
            to=email,
            subject=f"{inviter.full_name} invited you to {org.name} on Vyterlix",
            body=(
                f"Hi,\n\n{inviter.full_name} has invited you to join {org.name} on Vyterlix "
                f"as {'an' if role_code == 'owner' else 'a'} {role_code}.\n\n"
                f"To accept, open this link:\n{link}\n\n"
                f"If you don't have a Vyterlix account yet, you'll be asked to create one "
                f"using this email address ({email}). The invitation expires in "
                f"{settings.invitation_ttl_days} days. If you weren't expecting it, you "
                "can ignore this email."
            ),
        )
    )
    return _out(db, inv)


def list_invitations(db: Session) -> list[InvitationOut]:
    invitations = db.scalars(
        select(OrganizationInvitation).order_by(OrganizationInvitation.created_at.desc())
    ).all()
    return [_out(db, inv) for inv in invitations]


def revoke_invitation(
    db: Session, org: Organization, actor: User, invitation_id: uuid.UUID, meta: RequestMeta
) -> None:
    inv = db.scalar(
        select(OrganizationInvitation).where(OrganizationInvitation.id == invitation_id)
    )
    if inv is None:
        raise NotFoundError("Invitation not found", code="invitation_not_found")
    if inv.accepted_at is not None:
        raise ConflictError("That invitation has already been accepted", code="already_accepted")
    if inv.revoked_at is None:
        inv.revoked_at = func.now()
        record_audit(
            db,
            AuditAction.INVITATION_REVOKED,
            actor_user_id=actor.id,
            organization_id=org.id,
            target_type="invitation",
            target_id=inv.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )
        db.commit()


# --- invitee side (no organisation in scope yet) ----------------------------------------


def _open_invitation(db: Session, raw_token: str, *, lock: bool = False):
    """The invitation behind a link, if it can still be used, else None."""
    stmt = (
        select(OrganizationInvitation)
        .join(Organization, Organization.id == OrganizationInvitation.organization_id)
        .where(
            OrganizationInvitation.token_hash == hash_token(raw_token),
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
            OrganizationInvitation.expires_at > func.now(),
            Organization.status == "active",
        )
        # The token is the only way in; which organisation it belongs to is what we're finding out.
        .execution_options(**ACROSS_TENANTS)
    )
    if lock:
        stmt = stmt.with_for_update(of=OrganizationInvitation)
    return db.scalar(stmt)


def preview_invitation(db: Session, raw_token: str) -> InvitationPreviewOut:
    inv = _open_invitation(db, raw_token)
    if inv is None:
        raise _invalid_invitation()
    return InvitationPreviewOut(
        organization_name=inv.organization.name,
        email=inv.email,
        role=inv.role.code,
        invited_by=_inviter_name(db, inv),
        expires_at=inv.expires_at,
    )


def accept_invitation(
    db: Session, user: User, raw_token: str, meta: RequestMeta
) -> OrganizationOut:
    inv = _open_invitation(db, raw_token, lock=True)
    if inv is None:
        raise _invalid_invitation()
    if inv.email != user.email:
        # A forwarded link must not let someone else in. The link stays usable.
        # Read before rollback expires the row.
        invited_email, org_id, invitation_id = inv.email, inv.organization_id, inv.id
        db.rollback()  # leaves the link usable for the right person
        record_audit(
            db,
            AuditAction.INVITATION_ACCEPT_REJECTED,
            actor_user_id=user.id,
            organization_id=org_id,
            target_type="invitation",
            target_id=invitation_id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"reason": "email_mismatch"},
        )
        db.commit()
        raise PermissionDeniedError(
            f"This invitation was sent to a different email address. Log in as {invited_email} "
            "to accept it.",
            code="invitation_email_mismatch",
        )

    org = inv.organization
    role_code = inv.role.code
    with tenant_scope(db, org.id):
        membership = db.scalar(select(OrganizationUser).where(OrganizationUser.user_id == user.id))
        if membership is None:
            db.add(
                OrganizationUser(
                    user_id=user.id,
                    role_id=inv.role_id,
                    scope=inv.scope,
                    invited_by_user_id=inv.invited_by_user_id,
                )
            )
        else:
            # A suspended former member being invited back gets the invited role.
            membership.role_id = inv.role_id
            membership.scope = inv.scope
            membership.status = "active"

        inv.accepted_at = func.now()
        inv.accepted_by_user_id = user.id
        if user.email_verified_at is None:
            user.email_verified_at = func.now()  # opening the emailed link proves they own it
        record_audit(
            db,
            AuditAction.INVITATION_ACCEPTED,
            actor_user_id=user.id,
            organization_id=org.id,
            target_type="invitation",
            target_id=inv.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"role": role_code},
        )
        db.commit()
    db.refresh(org)
    return OrganizationOut(
        id=org.id, name=org.name, status=org.status, role=role_code, created_at=org.created_at
    )
