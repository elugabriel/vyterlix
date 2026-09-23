import uuid

import pytest
from sqlalchemy import select, update

from app.models.identity import AuditLog, Organization, OrganizationUser, Role, User

URL = "/api/v1/organizations"


def create(api, auth, name="Acme Retail"):
    return api.post(URL, json={"name": name}, headers=auth)


# --- creating ---------------------------------------------------------------------


def test_create_organization_makes_creator_the_owner(api, db, signup):
    auth = signup()
    res = create(api, auth, "  Acme Retail  ")
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Acme Retail"
    assert data["status"] == "active"
    assert data["role"] == "owner"

    org = db.get(Organization, uuid.UUID(data["id"]))
    assert org.data_improvement_opt_in is False
    membership = db.scalars(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    ).one()
    assert membership.user_id == org.created_by_user_id
    assert membership.status == "active"
    assert db.get(Role, membership.role_id).code == "owner"


def test_creation_is_audited_against_the_organization(api, db, signup):
    org_id = create(api, signup()).json()["id"]
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "organization.created")).one()
    assert str(entry.organization_id) == org_id
    assert str(entry.target_id) == org_id


def test_unverified_user_cannot_create_an_organization(api, db, signup):
    res = create(api, signup(verified=False))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "email_not_verified"
    assert db.scalars(select(Organization)).all() == []


def test_requires_login(api):
    assert create(api, {}).status_code == 401
    assert api.get(URL).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "Acme", "data_improvement_opt_in": True},  # can't opt in via creation
        {"name": "Acme", "status": "suspended"},
    ],
)
def test_invalid_creation_rejected(api, signup, body):
    assert api.post(URL, json=body, headers=signup()).status_code == 422


def test_one_user_can_create_several_businesses(api, signup):
    auth = signup()
    assert create(api, auth, "Shop One").status_code == 201
    assert create(api, auth, "Shop Two").status_code == 201
    names = [o["name"] for o in api.get(URL, headers=auth).json()]
    assert names == ["Shop One", "Shop Two"]


def test_different_users_may_use_the_same_business_name(api, signup):
    assert create(api, signup("a@acme.co.uk"), "Corner Shop").status_code == 201
    assert create(api, signup("b@acme.co.uk"), "Corner Shop").status_code == 201


# --- listing and reading ------------------------------------------------------------


def test_list_shows_only_my_organizations_with_my_role(api, db, signup):
    alice, bob = signup("alice@acme.co.uk"), signup("bob@acme.co.uk")
    create(api, alice, "Alice Ltd")
    bob_org = create(api, bob, "Bob Ltd").json()

    # Make Alice a viewer in Bob's business (invitations arrive in step 10).
    alice_id = db.scalars(select(User.id).where(User.email == "alice@acme.co.uk")).one()
    viewer = db.scalars(select(Role).where(Role.code == "viewer")).one()
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(bob_org["id"]), user_id=alice_id, role_id=viewer.id
        )
    )
    db.flush()

    mine = {o["name"]: o["role"] for o in api.get(URL, headers=alice).json()}
    assert mine == {"Alice Ltd": "owner", "Bob Ltd": "viewer"}
    assert [o["name"] for o in api.get(URL, headers=bob).json()] == ["Bob Ltd"]


def test_new_user_has_no_organizations(api, signup):
    assert api.get(URL, headers=signup()).json() == []


def test_suspended_membership_is_hidden(api, db, signup):
    auth = signup()
    org_id = create(api, auth).json()["id"]
    db.execute(update(OrganizationUser).values(status="suspended"))
    assert api.get(URL, headers=auth).json() == []
    assert api.get(f"{URL}/{org_id}", headers=auth).status_code == 404


def test_read_my_organization(api, signup):
    auth = signup()
    org = create(api, auth).json()
    res = api.get(f"{URL}/{org['id']}", headers=auth)
    assert res.status_code == 200
    assert res.json() == org


def test_other_peoples_organization_looks_like_it_does_not_exist(api, signup):
    owner, outsider = signup("owner@acme.co.uk"), signup("outsider@acme.co.uk")
    org_id = create(api, owner).json()["id"]

    theirs = api.get(f"{URL}/{org_id}", headers=outsider)
    missing = api.get(f"{URL}/{uuid.uuid4()}", headers=outsider)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"]["code"] == missing.json()["error"]["code"]
    assert theirs.json()["error"]["message"] == missing.json()["error"]["message"]


def test_malformed_organization_id_rejected(api, signup):
    assert api.get(f"{URL}/not-a-uuid", headers=signup()).status_code == 422
