from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import ACROSS_TENANTS
from app.models.identity import (
    Organization,
    OrganizationUser,
    Role,
    User,
    UserToken,
)


def make_user(db, email="owner@example.com", **kw) -> User:
    user = User(email=email, password_hash="x", full_name="Test User", **kw)
    db.add(user)
    db.flush()
    return user


def make_org(db, name="Acme Retail", created_by=None) -> Organization:
    org = Organization(name=name, created_by_user_id=created_by.id if created_by else None)
    db.add(org)
    db.flush()
    return org


def system_role(db, code: str) -> Role:
    return db.scalars(select(Role).where(Role.code == code, Role.organization_id.is_(None))).one()


def test_system_roles_are_seeded_with_expected_permissions(db):
    perms = {
        code: {p.code for p in system_role(db, code).permissions}
        for code in ("owner", "manager", "viewer")
    }
    assert perms["viewer"] == {"insights.view"}
    assert perms["manager"] == {"insights.view", "recommendations.action", "actions.manage"}
    assert perms["owner"] >= perms["manager"] | {
        "org.manage",
        "billing.manage",
        "members.manage",
        "data.manage",
    }
    # Only owners may manage members, billing, settings or data.
    assert not perms["manager"] & {"members.manage", "billing.manage", "org.manage", "data.manage"}


def test_new_user_and_org_get_safe_defaults(db):
    user = make_user(db)
    org = make_org(db, created_by=user)
    db.refresh(user)
    db.refresh(org)
    assert user.is_active is True
    assert user.email_verified_at is None
    assert org.status == "active"
    assert org.data_improvement_opt_in is False  # opt-in, default OFF


def test_email_must_be_stored_lowercase(db):
    with pytest.raises(IntegrityError, match="email_lowercase"):
        make_user(db, email="Owner@Example.com")


def test_duplicate_email_rejected(db):
    make_user(db)
    with pytest.raises(IntegrityError, match="uq_users_email"):
        make_user(db)


def test_user_can_belong_to_several_orgs_but_only_once_each(db):
    user = make_user(db)
    org_a, org_b = make_org(db, "A"), make_org(db, "B")
    owner = system_role(db, "owner")
    db.add_all(
        [
            OrganizationUser(organization_id=org_a.id, user_id=user.id, role_id=owner.id),
            OrganizationUser(organization_id=org_b.id, user_id=user.id, role_id=owner.id),
        ]
    )
    db.flush()
    db.refresh(user)
    assert {m.organization.name for m in user.memberships} == {"A", "B"}

    db.add(OrganizationUser(organization_id=org_a.id, user_id=user.id, role_id=owner.id))
    with pytest.raises(IntegrityError, match="uq_organization_users_organization_id_user_id"):
        db.flush()


def test_deleting_org_removes_its_memberships(db):
    user = make_user(db)
    org = make_org(db)
    db.add(
        OrganizationUser(
            organization_id=org.id, user_id=user.id, role_id=system_role(db, "owner").id
        )
    )
    db.flush()
    db.execute(delete(Organization).where(Organization.id == org.id))
    assert (
        db.scalars(
            select(OrganizationUser)
            .where(OrganizationUser.user_id == user.id)
            .execution_options(**ACROSS_TENANTS)
        ).all()
        == []
    )


def test_role_in_use_cannot_be_deleted(db):
    user, org = make_user(db), make_org(db)
    viewer = system_role(db, "viewer")
    db.add(OrganizationUser(organization_id=org.id, user_id=user.id, role_id=viewer.id))
    db.flush()
    with pytest.raises(IntegrityError, match="fk_organization_users_role_id_roles"):
        db.execute(delete(Role).where(Role.id == viewer.id))


def test_password_reset_token_can_be_stored(db):
    user = make_user(db)
    token = UserToken(
        user_id=user.id,
        purpose="password_reset",
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        requested_ip="203.0.113.7",
    )
    db.add(token)
    db.flush()
    db.refresh(token)
    assert token.used_at is None


def test_unknown_token_purpose_rejected(db):
    user = make_user(db)
    db.add(
        UserToken(
            user_id=user.id,
            purpose="magic_login",
            token_hash="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    with pytest.raises(IntegrityError, match="purpose_valid"):
        db.flush()
