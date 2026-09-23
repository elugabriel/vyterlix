import uuid

import pytest
from sqlalchemy import delete, select

from app.api.deps import Tenant
from app.core.permissions import KpiCategory, Perm
from app.models.identity import (
    AuditLog,
    Organization,
    OrganizationUser,
    Permission,
    Role,
    User,
    role_permissions,
)
from app.schemas.organizations import Remit

ORGS = "/api/v1/organizations"


def _role_id(db, code):
    return db.scalars(
        select(Role.id).where(Role.code == code, Role.organization_id.is_(None))
    ).one()


def _user_id(db, email):
    return db.scalars(select(User.id).where(User.email == email)).one()


@pytest.fixture
def team(api, db, signup):
    """One business: owner@, manager@, viewer@. Returns (org_id, {name: auth header})."""
    auth = {name: signup(f"{name}@acme.co.uk") for name in ("owner", "manager", "viewer")}
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    for name in ("manager", "viewer"):
        db.add(
            OrganizationUser(
                organization_id=uuid.UUID(org_id),
                user_id=_user_id(db, f"{name}@acme.co.uk"),
                role_id=_role_id(db, name),
            )
        )
    db.flush()
    return org_id, auth


def change_member(api, auth, org_id, user_id, body):
    return api.patch(f"{ORGS}/{org_id}/members/{user_id}", json=body, headers=auth)


# --- permission codes stay in sync with the database ------------------------------------


def test_permission_constants_match_the_database(db):
    assert {p.value for p in Perm} == set(db.scalars(select(Permission.code)).all())


# --- rename: org.manage (owner only) ------------------------------------------------


def test_owner_can_rename_the_business(api, db, team):
    org_id, auth = team
    res = api.patch(f"{ORGS}/{org_id}", json={"name": "Acme Trading"}, headers=auth["owner"])
    assert res.status_code == 200
    assert res.json()["name"] == "Acme Trading"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "organization.updated")).one()
    assert entry.details == {"fields": ["name"]}


@pytest.mark.parametrize("who", ["manager", "viewer"])
def test_non_owners_cannot_rename(api, db, team, who):
    org_id, auth = team
    res = api.patch(f"{ORGS}/{org_id}", json={"name": "Hacked"}, headers=auth[who])
    assert res.status_code == 403
    err = res.json()["error"]
    assert err["code"] == "permission_denied"
    assert err["details"] == {"required_permission": "org.manage"}
    assert db.get(Organization, uuid.UUID(org_id)).name == "Acme"


@pytest.mark.parametrize("who", ["owner", "manager", "viewer"])
def test_every_member_can_read_the_business_and_its_members(api, team, who):
    org_id, auth = team
    assert api.get(f"{ORGS}/{org_id}", headers=auth[who]).json()["role"] == who
    assert len(api.get(f"{ORGS}/{org_id}/members", headers=auth[who]).json()) == 3


def test_permissions_come_from_the_database_not_the_code(api, db, team):
    """Taking org.manage away from the owner role in the DB removes the ability."""
    org_id, auth = team
    db.execute(
        delete(role_permissions).where(
            role_permissions.c.role_id == _role_id(db, "owner"),
            role_permissions.c.permission_id
            == db.scalars(select(Permission.id).where(Permission.code == "org.manage")).one(),
        )
    )
    res = api.patch(f"{ORGS}/{org_id}", json={"name": "Nope"}, headers=auth["owner"])
    assert res.status_code == 403


# --- changing roles and remits: members.manage (owner only) --------------------------------


def test_owner_promotes_viewer_to_manager_with_a_remit(api, db, team):
    org_id, auth = team
    viewer_id = _user_id(db, "viewer@acme.co.uk")
    res = change_member(
        api,
        auth["owner"],
        org_id,
        viewer_id,
        {"role": "manager", "remit": {"kpi_categories": ["sales", "marketing"]}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "manager"
    assert body["remit"] == {"kpi_categories": ["sales", "marketing"]}

    listed = {
        m["email"]: m for m in api.get(f"{ORGS}/{org_id}/members", headers=auth["owner"]).json()
    }
    assert listed["viewer@acme.co.uk"]["remit"] == {"kpi_categories": ["sales", "marketing"]}

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "member.updated")).one()
    assert entry.details["role"] == {"from": "viewer", "to": "manager"}
    assert str(entry.target_id) == str(viewer_id)


def test_promoted_member_immediately_gains_the_permission(api, db, team):
    org_id, auth = team
    change_member(api, auth["owner"], org_id, _user_id(db, "viewer@acme.co.uk"), {"role": "owner"})
    res = api.patch(f"{ORGS}/{org_id}", json={"name": "Renamed"}, headers=auth["viewer"])
    assert res.status_code == 200


def test_remit_rejected_for_non_managers(api, db, team):
    org_id, auth = team
    res = change_member(
        api,
        auth["owner"],
        org_id,
        _user_id(db, "viewer@acme.co.uk"),
        {"remit": {"kpi_categories": ["sales"]}},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "remit_only_for_managers"


def test_demoting_a_manager_clears_their_remit(api, db, team):
    org_id, auth = team
    manager_id = _user_id(db, "manager@acme.co.uk")
    change_member(api, auth["owner"], org_id, manager_id, {"remit": {"kpi_categories": ["sales"]}})
    res = change_member(api, auth["owner"], org_id, manager_id, {"role": "viewer"})
    assert res.json()["remit"] is None


@pytest.mark.parametrize("who", ["manager", "viewer"])
def test_non_owners_cannot_change_roles(api, db, team, who):
    org_id, auth = team
    res = change_member(
        api, auth[who], org_id, _user_id(db, f"{who}@acme.co.uk"), {"role": "owner"}
    )
    assert res.status_code == 403
    assert res.json()["error"]["details"] == {"required_permission": "members.manage"}


def test_sole_owner_cannot_step_down(api, db, team):
    org_id, auth = team
    res = change_member(
        api, auth["owner"], org_id, _user_id(db, "owner@acme.co.uk"), {"role": "viewer"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "last_owner"


def test_with_two_owners_one_can_step_down_but_not_the_last(api, db, team):
    org_id, auth = team
    owner_id, viewer_id = _user_id(db, "owner@acme.co.uk"), _user_id(db, "viewer@acme.co.uk")
    assert (
        change_member(api, auth["owner"], org_id, viewer_id, {"role": "owner"}).status_code == 200
    )
    assert (
        change_member(api, auth["owner"], org_id, owner_id, {"role": "manager"}).status_code == 200
    )
    # The original owner is now a manager and can no longer manage members...
    assert (
        change_member(api, auth["owner"], org_id, viewer_id, {"role": "viewer"}).status_code == 403
    )
    # ...and the new owner is the last one, so can't step down.
    last = change_member(api, auth["viewer"], org_id, viewer_id, {"role": "viewer"})
    assert last.json()["error"]["code"] == "last_owner"


def test_cannot_change_someone_outside_the_business(api, db, team, signup):
    org_id, auth = team
    signup("stranger@acme.co.uk")
    res = change_member(
        api, auth["owner"], org_id, _user_id(db, "stranger@acme.co.uk"), {"role": "viewer"}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "member_not_found"


def test_cannot_reach_a_member_of_another_business_by_id(api, db, team, signup):
    """Tenant scoping: another business's member is invisible even with their real id."""
    org_id, auth = team
    other = signup("other-owner@acme.co.uk")
    api.post(ORGS, json={"name": "Other Ltd"}, headers=other)
    res = change_member(
        api, auth["owner"], org_id, _user_id(db, "other-owner@acme.co.uk"), {"role": "viewer"}
    )
    assert res.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"role": None},
        {"role": "admin"},
        {"remit": {"kpi_categories": []}},
        {"remit": {"kpi_categories": ["astrology"]}},
        {"remit": {"business_units": ["north"]}},
        {"role": "manager", "status": "suspended"},
    ],
)
def test_invalid_member_updates_rejected(api, db, team, body):
    org_id, auth = team
    res = change_member(api, auth["owner"], org_id, _user_id(db, "manager@acme.co.uk"), body)
    assert res.status_code == 422


# --- remit logic -----------------------------------------------------------------------


def _tenant(role, perms, remit=None):
    return Tenant(
        organization=None,
        user=None,
        role=role,
        permissions=frozenset(perms),
        remit=remit,
        session_id=uuid.uuid4(),
    )


def test_manager_can_only_act_within_remit():
    manager = _tenant(
        "manager",
        {Perm.RECOMMENDATIONS_ACTION},
        Remit(kpi_categories=[KpiCategory.SALES]),
    )
    assert manager.can(Perm.RECOMMENDATIONS_ACTION, KpiCategory.SALES)
    assert not manager.can(Perm.RECOMMENDATIONS_ACTION, KpiCategory.FINANCIAL)
    assert not manager.can(Perm.MEMBERS_MANAGE)


def test_manager_without_remit_is_unrestricted_by_category():
    manager = _tenant("manager", {Perm.RECOMMENDATIONS_ACTION})
    assert all(manager.can(Perm.RECOMMENDATIONS_ACTION, c) for c in KpiCategory)


def test_owner_ignores_remit_and_viewer_never_acts():
    owner = _tenant("owner", set(Perm), Remit(kpi_categories=[KpiCategory.SALES]))
    viewer = _tenant("viewer", {Perm.INSIGHTS_VIEW})
    assert owner.can(Perm.RECOMMENDATIONS_ACTION, KpiCategory.FINANCIAL)
    assert not any(viewer.can(Perm.RECOMMENDATIONS_ACTION, c) for c in KpiCategory)
