"""Creating organisations (the customer's business) and listing a user's memberships."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.tenant import ACROSS_TENANTS
from app.models.identity import Organization, OrganizationUser, Role, User
from app.schemas.organizations import MemberOut, OrganizationOut
from app.services.audit import record_audit
from app.services.auth import RequestMeta


def _system_role(db: Session, code: str) -> Role:
    return db.scalars(select(Role).where(Role.code == code, Role.organization_id.is_(None))).one()


def _membership_query(user_id: uuid.UUID):
    """Organisations this user actively belongs to, with their role code.

    Deliberately spans organisations (it's how we find out which ones the user may enter),
    so it opts out of automatic tenant scoping. Always filtered by the user's own id.
    """
    return (
        select(Organization, Role.code)
        .join(OrganizationUser, OrganizationUser.organization_id == Organization.id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.user_id == user_id, OrganizationUser.status == "active")
        .execution_options(**ACROSS_TENANTS)
    )


def _out(org: Organization, role: str) -> OrganizationOut:
    return OrganizationOut(
        id=org.id, name=org.name, status=org.status, role=role, created_at=org.created_at
    )


def create_organization(db: Session, user: User, name: str, meta: RequestMeta) -> OrganizationOut:
    """Create a business; its creator becomes the Owner."""
    org = Organization(name=name, created_by_user_id=user.id)
    db.add(org)
    db.flush()
    db.add(
        OrganizationUser(
            organization_id=org.id, user_id=user.id, role_id=_system_role(db, "owner").id
        )
    )
    record_audit(
        db,
        "organization.created",
        actor_user_id=user.id,
        organization_id=org.id,
        target_type="organization",
        target_id=org.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()
    db.refresh(org)
    return _out(org, "owner")


def list_organizations(db: Session, user: User) -> list[OrganizationOut]:
    rows = db.execute(_membership_query(user.id).order_by(Organization.name, Organization.id))
    return [_out(org, role) for org, role in rows]


def resolve_membership(
    db: Session, user: User, organization_id: uuid.UUID
) -> tuple[Organization, str]:
    """The organisation and the user's role in it, if they may enter it.

    Non-members and closed organisations get 404, not 403, so outsiders can't probe
    which IDs exist. Members of a suspended organisation are told why they can't enter.
    """
    row = db.execute(_membership_query(user.id).where(Organization.id == organization_id)).first()
    if row is None or row[0].status == "closed":
        raise NotFoundError("Organisation not found", code="organization_not_found")
    org, role = row
    if org.status != "active":
        raise AppError(
            "This organisation is suspended", code="organization_suspended", status_code=403
        )
    return org, role


def organization_out(org: Organization, role: str) -> OrganizationOut:
    return _out(org, role)


def list_members(db: Session) -> list[MemberOut]:
    """Members of the organisation the session is scoped to.

    Note there is no organisation filter here: tenant scoping adds it automatically
    (app/db/tenant.py). Called without a tenant in scope, this raises instead of leaking.
    """
    rows = db.execute(
        select(OrganizationUser, User, Role.code)
        .join(User, User.id == OrganizationUser.user_id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .order_by(User.full_name, User.id)
    )
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=role,
            status=membership.status,
            joined_at=membership.created_at,
        )
        for membership, user, role in rows
    ]
