import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.security import hash_token
from app.db.tenant import ACROSS_TENANTS
from app.models.identity import (
    AuditLog,
    Organization,
    OrganizationInvitation,
    OrganizationUser,
    Role,
    User,
)

ORGS = "/api/v1/organizations"
TEST_PASSWORD = "correct horse battery"  # same as the signup fixture


def _user_id(db, email):
    return db.scalars(select(User.id).where(User.email == email)).one()


def _role_id(db, code):
    return db.scalars(
        select(Role.id).where(Role.code == code, Role.organization_id.is_(None))
    ).one()


@pytest.fixture
def org(api, signup):
    """An organisation owned by owner@. Returns (org_id, owner auth header)."""
    owner = signup("owner@acme.co.uk")
    org_id = api.post(ORGS, json={"name": "Acme Retail"}, headers=owner).json()["id"]
    return org_id, owner


def invite(api, auth, org_id, email="new@acme.co.uk", role="viewer", **extra):
    return api.post(
        f"{ORGS}/{org_id}/invitations", json={"email": email, "role": role, **extra}, headers=auth
    )


def link_token(outbox, to="new@acme.co.uk"):
    msg = [m for m in outbox if m.to == to and "invited you" in m.subject][-1]
    return re.search(r"accept-invite\.html\?token=([A-Za-z0-9_\-]+)", msg.body).group(1)


def accept(api, auth, token):
    return api.post("/api/v1/invitations/accept", json={"token": token}, headers=auth)


def open_invites(db):
    return db.scalars(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.accepted_at.is_(None))
        .where(OrganizationInvitation.revoked_at.is_(None))
        .execution_options(**ACROSS_TENANTS)
    ).all()


# --- inviting ----------------------------------------------------------------------


def test_owner_invites_by_email(api, db, org, outbox):
    org_id, owner = org
    res = invite(api, owner, org_id, "New@ACME.co.uk", "manager")
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "new@acme.co.uk"
    assert body["role"] == "manager"
    assert body["status"] == "pending"
    assert body["invited_by"] == "owner"
    assert "token" not in str(body).lower()

    msg = outbox[-1]
    assert msg.to == "new@acme.co.uk"
    assert msg.subject == "owner invited you to Acme Retail on Vyterlix"
    assert "as a manager" in msg.body and "7 days" in msg.body
    stored = open_invites(db)[0]
    assert stored.token_hash == hash_token(link_token(outbox))


@pytest.mark.parametrize("who", ["manager", "viewer"])
def test_only_owners_can_invite(api, db, org, signup, who):
    org_id, _ = org
    auth = signup(f"{who}@acme.co.uk")
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=_user_id(db, f"{who}@acme.co.uk"),
            role_id=_role_id(db, who),
        )
    )
    db.flush()
    res = invite(api, auth, org_id)
    assert res.status_code == 403
    assert res.json()["error"]["details"] == {"required_permission": "members.manage"}


def test_cannot_invite_an_existing_member(api, org):
    org_id, owner = org
    res = invite(api, owner, org_id, "owner@acme.co.uk")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "already_member"


def test_reinviting_replaces_the_old_link(api, db, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id, role="viewer")
    first = link_token(outbox)
    invite(api, owner, org_id, role="manager")
    second = link_token(outbox)
    assert len(open_invites(db)) == 1

    newcomer = signup("new@acme.co.uk")
    assert accept(api, newcomer, first).json()["error"]["code"] == "invalid_invitation"
    assert accept(api, newcomer, second).json()["organization"]["role"] == "manager"


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "viewer", "remit": {"kpi_categories": ["sales"]}},
        {"role": "admin"},
        {"role": "manager", "remit": {"kpi_categories": []}},
        {"role": "viewer", "email_verified": True},
    ],
)
def test_invalid_invitations_rejected(api, org, extra):
    org_id, owner = org
    body = {"email": "new@acme.co.uk", **extra}
    assert api.post(f"{ORGS}/{org_id}/invitations", json=body, headers=owner).status_code == 422


def test_invite_is_audited(api, db, org):
    org_id, owner = org
    invite(api, owner, org_id)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "invitation.created")).one()
    assert str(entry.organization_id) == org_id
    assert entry.details == {"email": "new@acme.co.uk", "role": "viewer"}


# --- previewing ----------------------------------------------------------------------


def test_preview_shows_who_invited_you_to_what(api, org, outbox):
    org_id, owner = org
    invite(api, owner, org_id, role="manager")
    res = api.post("/api/v1/invitations/preview", json={"token": link_token(outbox)})
    assert res.status_code == 200
    assert res.json() | {"expires_at": None} == {
        "organization_name": "Acme Retail",
        "email": "new@acme.co.uk",
        "role": "manager",
        "invited_by": "owner",
        "expires_at": None,
    }


def test_preview_of_a_bad_link(api):
    res = api.post("/api/v1/invitations/preview", json={"token": "x" * 43})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_invitation"


# --- accepting -------------------------------------------------------------------------


def test_brand_new_user_registers_and_accepts(api, db, org, outbox):
    org_id, owner = org
    invite(api, owner, org_id, role="manager", remit={"kpi_categories": ["sales"]})
    token = link_token(outbox)

    # They don't have an account: register (unverified) and log in, then accept.
    api.post(
        "/api/v1/auth/register",
        json={"email": "new@acme.co.uk", "password": TEST_PASSWORD, "full_name": "Newbie"},
    )
    login = api.post(
        "/api/v1/auth/login", json={"email": "new@acme.co.uk", "password": TEST_PASSWORD}
    )
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = accept(api, auth, token)
    assert res.status_code == 200
    assert res.json()["organization"]["name"] == "Acme Retail"
    assert res.json()["organization"]["role"] == "manager"

    user = db.scalars(select(User).where(User.email == "new@acme.co.uk")).one()
    assert user.email_verified_at is not None  # the emailed link proved the address

    members = api.get(f"{ORGS}/{org_id}/members", headers=auth).json()
    me = next(m for m in members if m["email"] == "new@acme.co.uk")
    assert me["role"] == "manager"
    assert me["remit"] == {"kpi_categories": ["sales"]}
    assert [o["name"] for o in api.get(ORGS, headers=auth).json()] == ["Acme Retail"]


def test_accepting_is_audited_and_marks_the_invitation(api, db, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    newcomer = signup("new@acme.co.uk")
    accept(api, newcomer, link_token(outbox))

    inv = db.scalars(select(OrganizationInvitation).execution_options(**ACROSS_TENANTS)).one()
    assert inv.accepted_at is not None
    assert inv.accepted_by_user_id == _user_id(db, "new@acme.co.uk")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "invitation.accepted")).one()
    assert str(entry.organization_id) == org_id


def test_forwarded_link_does_not_work_for_someone_else(api, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    token = link_token(outbox)

    res = accept(api, signup("someone-else@acme.co.uk"), token)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "invitation_email_mismatch"
    # The right person can still use it.
    assert accept(api, signup("new@acme.co.uk"), token).status_code == 200


def test_link_works_only_once(api, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    token = link_token(outbox)
    newcomer = signup("new@acme.co.uk")
    assert accept(api, newcomer, token).status_code == 200
    assert accept(api, newcomer, token).status_code == 400


def test_expired_link_rejected(api, db, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    db.execute(
        update(OrganizationInvitation)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        .execution_options(**ACROSS_TENANTS)
    )
    res = accept(api, signup("new@acme.co.uk"), link_token(outbox))
    assert res.json()["error"]["code"] == "invalid_invitation"


def test_link_to_a_suspended_business_rejected(api, db, org, signup, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    db.execute(update(Organization).values(status="suspended"))
    assert accept(api, signup("new@acme.co.uk"), link_token(outbox)).status_code == 400


def test_accepting_requires_login(api, org, outbox):
    org_id, owner = org
    invite(api, owner, org_id)
    assert accept(api, {}, link_token(outbox)).status_code == 401


def test_suspended_former_member_is_reactivated_with_the_new_role(api, db, org, signup, outbox):
    org_id, owner = org
    newcomer = signup("new@acme.co.uk")
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=_user_id(db, "new@acme.co.uk"),
            role_id=_role_id(db, "viewer"),
            status="suspended",
        )
    )
    db.flush()
    invite(api, owner, org_id, role="manager")  # suspended members may be re-invited
    res = accept(api, newcomer, link_token(outbox))
    assert res.json()["organization"]["role"] == "manager"
    assert api.get(f"{ORGS}/{org_id}", headers=newcomer).status_code == 200


# --- listing and revoking ------------------------------------------------------------------


def test_list_shows_every_status(api, db, org, signup, outbox):
    org_id, owner = org
    for email in ("pending", "revoked", "accepted", "expired"):
        invite(api, owner, org_id, f"{email}@acme.co.uk")
    invites = {i["email"]: i for i in api.get(f"{ORGS}/{org_id}/invitations", headers=owner).json()}

    api.delete(f"{ORGS}/{org_id}/invitations/{invites['revoked@acme.co.uk']['id']}", headers=owner)
    accept(api, signup("accepted@acme.co.uk"), link_token(outbox, "accepted@acme.co.uk"))
    db.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.email == "expired@acme.co.uk")
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        .execution_options(**ACROSS_TENANTS)
    )

    statuses = {
        i["email"].split("@")[0]: i["status"]
        for i in api.get(f"{ORGS}/{org_id}/invitations", headers=owner).json()
    }
    assert statuses == {
        "pending": "pending",
        "revoked": "revoked",
        "accepted": "accepted",
        "expired": "expired",
    }


def test_revoked_link_stops_working(api, org, signup, outbox):
    org_id, owner = org
    inv_id = invite(api, owner, org_id).json()["id"]
    assert api.delete(f"{ORGS}/{org_id}/invitations/{inv_id}", headers=owner).status_code == 204
    res = accept(api, signup("new@acme.co.uk"), link_token(outbox))
    assert res.json()["error"]["code"] == "invalid_invitation"


def test_cannot_revoke_an_accepted_invitation(api, org, signup, outbox):
    org_id, owner = org
    inv_id = invite(api, owner, org_id).json()["id"]
    accept(api, signup("new@acme.co.uk"), link_token(outbox))
    res = api.delete(f"{ORGS}/{org_id}/invitations/{inv_id}", headers=owner)
    assert res.status_code == 409


def test_other_businesses_cannot_see_or_revoke_these_invitations(api, org, signup):
    org_id, owner = org
    inv_id = invite(api, owner, org_id).json()["id"]

    rival = signup("rival@acme.co.uk")
    rival_org = api.post(ORGS, json={"name": "Rival"}, headers=rival).json()["id"]
    assert api.get(f"{ORGS}/{rival_org}/invitations", headers=rival).json() == []
    # Using their own business in the URL with our invitation's id:
    res = api.delete(f"{ORGS}/{rival_org}/invitations/{inv_id}", headers=rival)
    assert res.status_code == 404
    # Using our business in the URL: they're not a member.
    assert api.get(f"{ORGS}/{org_id}/invitations", headers=rival).status_code == 404
