"""Creating organisations (the customer's business) and listing a user's memberships."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.identity import Organization, OrganizationUser, Role, User
from app.schemas.organizations import OrganizationOut
from app.services.audit import record_audit
from app.services.auth import RequestMeta


def _system_role(db: Session, code: str) -> Role:
    return db.scalars(select(Role).where(Role.code == code, Role.organization_id.is_(None))).one()


def _membership_query(user_id: uuid.UUID):
    """Organisations this user actively belongs to, with their role code."""
    return (
        select(Organization, Role.code)
        .join(OrganizationUser, OrganizationUser.organization_id == Organization.id)
        .join(Role, Role.id == OrganizationUser.role_id)
        .where(OrganizationUser.user_id == user_id, OrganizationUser.status == "active")
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


def get_organization(db: Session, user: User, organization_id: uuid.UUID) -> OrganizationOut:
    """Only for members. Non-members get 404, not 403, so they can't probe which IDs exist."""
    row = db.execute(_membership_query(user.id).where(Organization.id == organization_id)).first()
    if row is None:
        raise NotFoundError("Organisation not found", code="organization_not_found")
    return _out(*row)
