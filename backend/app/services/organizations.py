"""Organisations (the customer's business), memberships, roles and remits."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.db.tenant import ACROSS_TENANTS
from app.models.identity import Organization, OrganizationUser, Role, User
from app.schemas.organizations import MemberOut, OrganizationOut, Remit
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


@dataclass(frozen=True)
class Membership:
    organization: Organization
    role: str
    permissions: frozenset[str]
    remit: Remit | None


def resolve_membership(db: Session, user: User, organization_id: uuid.UUID) -> Membership:
    """The organisation, the user's role, permissions and remit, if they may enter it.

    Non-members and closed organisations get 404, not 403, so outsiders can't probe
    which IDs exist. Members of a suspended organisation are told why they can't enter.
    """
    row = db.execute(
        _membership_query(user.id)
        .add_columns(OrganizationUser.role_id, OrganizationUser.scope)
        .where(Organization.id == organization_id)
    ).first()
    if row is None or row[0].status == "closed":
        raise NotFoundError("Organisation not found", code="organization_not_found")
    org, role_code, role_id, scope = row
    if org.status != "active":
        raise AppError(
            "This organisation is suspended", code="organization_suspended", status_code=403
        )
    permissions = frozenset(p.code for p in db.get(Role, role_id).permissions)
    remit = Remit.model_validate(scope) if scope else None
    return Membership(org, role_code, permissions, remit)


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
            remit=Remit.model_validate(membership.scope) if membership.scope else None,
            status=membership.status,
            joined_at=membership.created_at,
        )
        for membership, user, role in rows
    ]


def rename_organization(db: Session, org: Organization, actor: User, name: str, meta: RequestMeta):
    if org.name != name:
        org.name = name
        record_audit(
            db,
            "organization.updated",
            actor_user_id=actor.id,
            organization_id=org.id,
            target_type="organization",
            target_id=org.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"fields": ["name"]},
        )
        db.commit()
        db.refresh(org)
    return org


def update_member(
    db: Session,
    organization_id: uuid.UUID,
    actor: User,
    target_user_id: uuid.UUID,
    changes: dict,
    meta: RequestMeta,
) -> MemberOut:
    """Change a member's role and/or remit. The session must be scoped to the organisation.

    `changes` holds only the fields the caller sent: "role" (code) and/or "remit" (dict|None).
    """
    membership = db.scalar(
        select(OrganizationUser).where(OrganizationUser.user_id == target_user_id).with_for_update()
    )
    if membership is None:
        raise NotFoundError("Member not found", code="member_not_found")

    old_role = db.get(Role, membership.role_id).code
    new_role = changes.get("role", old_role)
    if changes.get("remit") is not None and new_role != "manager":
        raise AppError(
            "A remit can only be set for managers",
            code="remit_only_for_managers",
            status_code=422,
        )

    if old_role == "owner" and new_role != "owner":
        # Lock every active owner row so two owners can't demote each other at once.
        owners = db.scalars(
            select(OrganizationUser)
            .join(Role, Role.id == OrganizationUser.role_id)
            .where(Role.code == "owner", OrganizationUser.status == "active")
            .with_for_update(of=OrganizationUser)
        ).all()
        if len(owners) <= 1:
            raise AppError(
                "A business must always have at least one owner. Make someone else an owner first.",
                code="last_owner",
                status_code=409,
            )

    details: dict = {}
    if new_role != old_role:
        membership.role_id = _system_role(db, new_role).id
        details["role"] = {"from": old_role, "to": new_role}
    if new_role != "manager" and membership.scope is not None:
        membership.scope = None  # remits only mean something for managers
        details["remit"] = None
    if "remit" in changes and new_role == "manager":
        new_scope = changes["remit"]
        if new_scope != membership.scope:
            membership.scope = new_scope
            details["remit"] = new_scope

    if details:
        record_audit(
            db,
            "member.updated",
            actor_user_id=actor.id,
            organization_id=organization_id,
            target_type="user",
            target_id=target_user_id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details=details,
        )
        db.commit()

    user = db.get(User, target_user_id)
    db.refresh(membership)
    return MemberOut(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=new_role,
        remit=Remit.model_validate(membership.scope) if membership.scope else None,
        status=membership.status,
        joined_at=membership.created_at,
    )
