# ruff: noqa: E501, E731
"""The admin portal: only platform staff, only what they need to see, every look and change recorded."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.admin import PlatformStaff
from app.models.billing import Plan, Subscription
from app.models.identity import AuditLog, Organization, OrganizationUser, User
from app.services import admin as service
from tests.conftest import TEST_PASSWORD
from tests.test_health import ORGS, scoped

A = "/api/v1/admin"


def make_staff(api, db, signup, email="staff@vyterlix.com", role="admin"):
    auth = signup(email)
    service.grant_staff(db, email, role)
    return auth


def audit_actions(db, action):
    return db.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    ).all()


# --- who may come in -------------------------------------------------------------------------------------

READS = ["/me-not-here", "/organizations", "/users", "/audit-log", "/plans"]


@pytest.mark.parametrize("path", ["/organizations", "/users", "/audit-log", "/plans"])
def test_a_person_who_is_not_staff_is_told_the_page_does_not_exist(api, db, business, path):
    res = api.get(f"{A}{path}", headers=business[2]["owner"])
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"
    detail = api.get(f"{A}/organizations/{business[0]}", headers=business[2]["owner"])
    assert detail.status_code == 404


def test_nobody_is_let_in_without_logging_in_or_verifying_their_email(api, db, signup):
    assert api.get(f"{A}/organizations").status_code == 401
    unverified = signup("new@vyterlix.com", verified=False)
    service.grant_staff(db, "new@vyterlix.com", "admin")
    assert api.get(f"{A}/organizations", headers=unverified).status_code == 403


def test_the_website_can_ask_whether_someone_is_staff(api, db, signup, business):
    assert api.get(f"{A}/me", headers=business[2]["owner"]).json() == {
        "is_staff": False,
        "role": None,
    }
    staff = make_staff(api, db, signup, role="support")
    assert api.get(f"{A}/me", headers=staff).json() == {"is_staff": True, "role": "support"}
    assert api.get(f"{A}/me").status_code == 401


def test_staff_who_have_had_their_rights_taken_away_are_shut_out_at_once(api, db, signup, business):
    staff = make_staff(api, db, signup)
    assert api.get(f"{A}/organizations", headers=staff).status_code == 200
    service.revoke_staff(db, "staff@vyterlix.com")
    assert api.get(f"{A}/organizations", headers=staff).status_code == 404
    assert api.get(f"{A}/me", headers=staff).json()["is_staff"] is False


def test_support_staff_can_look_but_not_change_anything(api, db, signup, business):
    org, _, auth = business
    support = make_staff(api, db, signup, role="support")
    for path in ("/organizations", f"/organizations/{org}", "/users", "/audit-log", "/plans"):
        assert api.get(f"{A}{path}", headers=support).status_code == 200, path
    viewer_id = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    why = {"reason": "Because the customer asked"}
    changes = [
        ("post", f"/organizations/{org}/suspend", why),
        ("post", f"/organizations/{org}/reactivate", why),
        ("put", f"/organizations/{org}/members/{viewer_id}/role", {"role": "manager", **why}),
        ("post", f"/organizations/{org}/extend-trial", {"days": 5, **why}),
        ("post", f"/users/{viewer_id}/disable", why),
        ("post", f"/users/{viewer_id}/enable", why),
        ("patch", "/plans/starter", {"price_month_pence": 1, **why}),
        ("put", "/plans/starter/features/members", {"enabled": True, "limit": 9, **why}),
    ]
    for method, path, body in changes:
        res = getattr(api, method)(f"{A}{path}", json=body, headers=support)
        assert res.status_code == 403 and res.json()["error"]["code"] == "admin_only", path
    assert (
        db.scalars(select(Organization.status).where(Organization.id == uuid.UUID(org))).one()
        == "active"
    )


def test_every_change_needs_a_reason_of_some_substance(api, db, signup, business):
    staff = make_staff(api, db, signup)
    org = business[0]
    for reason in ("", "no", "    ", "x" * 501):
        res = api.post(f"{A}/organizations/{org}/suspend", json={"reason": reason}, headers=staff)
        assert res.status_code == 422, reason
    assert api.post(f"{A}/organizations/{org}/suspend", json={}, headers=staff).status_code == 422
    assert (
        api.post(
            f"{A}/organizations/{org}/suspend",
            json={"reason": "fine reason", "extra": 1},
            headers=staff,
        ).status_code
        == 422
    )


# --- businesses ---------------------------------------------------------------------------------------------


def test_the_list_of_businesses_shows_their_size_and_plan(api, db, signup, business):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    body = api.get(f"{A}/organizations", headers=staff).json()
    rows = {r["name"]: r for r in body["items"]}
    assert body["total"] == 2 and set(rows) == {"Acme", "Rival"}
    assert (rows["Acme"]["member_count"], rows["Rival"]["member_count"]) == (2, 1)
    assert (
        rows["Acme"]["plan_name"] is None and rows["Acme"]["plan_status"] is None
    )  # nobody has looked at billing yet
    api.get(f"{ORGS}/{org}/billing", headers=auth["owner"])
    again = {r["name"]: r for r in api.get(f"{A}/organizations", headers=staff).json()["items"]}
    assert (again["Acme"]["plan_name"], again["Acme"]["plan_status"]) == ("Scale", "trialing")


def test_businesses_can_be_searched_filtered_and_paged(api, db, signup, business):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    names = lambda params: [
        r["name"] for r in api.get(f"{A}/organizations{params}", headers=staff).json()["items"]
    ]  # noqa: E731
    assert names("?q=acm") == ["Acme"] and names("?q=RIV") == ["Rival"] and names("?q=zzz") == []
    assert names("?q=%25") == [] and names("?q=_") == []  # wildcards are plain characters
    assert names("?status=active") == ["Acme", "Rival"] and names("?status=suspended") == []
    assert api.get(f"{A}/organizations?status=weird", headers=staff).status_code == 422
    page = api.get(f"{A}/organizations?limit=1&offset=1", headers=staff).json()
    assert page["total"] == 2 and [r["name"] for r in page["items"]] == ["Rival"]
    assert api.get(f"{A}/organizations?limit=0", headers=staff).status_code == 422
    assert api.get(f"{A}/organizations?limit=101", headers=staff).status_code == 422
    assert api.get(f"{A}/organizations?offset=-1", headers=staff).status_code == 422


def test_one_business_shows_its_people_plan_and_use_but_nothing_from_inside_it(
    api, db, signup, business
):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    api.post(
        f"{ORGS}/{org}/invitations",
        json={"email": "new@acme.co.uk", "role": "viewer"},
        headers=auth["owner"],
    )
    api.get(f"{ORGS}/{org}/billing", headers=auth["owner"])
    res = api.get(f"{A}/organizations/{org}", headers=staff)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "id",
        "name",
        "status",
        "created_at",
        "created_by_email",
        "members",
        "subscription",
        "usage",
        "last_activity_at",
    }
    assert body["name"] == "Acme" and body["created_by_email"] == "owner@acme.co.uk"
    members = {m["email"]: m for m in body["members"]}
    assert set(members) == {"owner@acme.co.uk", "viewer@acme.co.uk"}
    assert (members["owner@acme.co.uk"]["role"], members["viewer@acme.co.uk"]["role"]) == (
        "owner",
        "viewer",
    )
    assert set(members["owner@acme.co.uk"]) == {
        "user_id",
        "email",
        "full_name",
        "role",
        "status",
        "last_login_at",
        "user_active",
    }
    assert body["usage"] == {
        "members": 3,
        "integrations": 0,
        "scheduled_reports": 0,
    }  # two people and an invitation
    assert (
        body["subscription"]["plan_code"] == "scale"
        and body["subscription"]["status"] == "trialing"
    )
    assert body["last_activity_at"] is not None
    text = res.text
    assert "password" not in text and "token" not in text.lower()


def test_a_business_that_has_not_looked_at_its_plan_has_none_to_show(api, db, signup, business):
    staff = make_staff(api, db, signup)
    body = api.get(f"{A}/organizations/{business[1]}", headers=staff).json()
    assert body["subscription"] is None and body["usage"]["members"] == 1


def test_looking_at_a_business_or_a_person_is_written_in_the_audit_log(api, db, signup, business):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    staff_id = db.scalars(select(User.id).where(User.email == "staff@vyterlix.com")).one()
    owner_id = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    api.get(f"{A}/organizations/{org}", headers=staff)
    api.get(f"{A}/users/{owner_id}", headers=staff)
    [seen_org] = audit_actions(db, "admin.organization_viewed")
    assert (seen_org.actor_user_id, str(seen_org.organization_id), seen_org.target_id) == (
        staff_id,
        org,
        org,
    )
    [seen_user] = audit_actions(db, "admin.user_viewed")
    assert (seen_user.actor_user_id, seen_user.target_id) == (staff_id, str(owner_id))
    # the business's own owner can see that staff looked
    log = api.get(f"{ORGS}/{org}/audit-log", headers=auth["owner"]).json()["entries"]
    assert "admin.organization_viewed" in [e["action"] for e in log]


def test_an_unknown_business_or_person_is_not_found(api, db, signup, business):
    staff = make_staff(api, db, signup)
    nobody = uuid.uuid4()
    assert api.get(f"{A}/organizations/{nobody}", headers=staff).status_code == 404
    assert api.get(f"{A}/users/{nobody}", headers=staff).status_code == 404
    assert api.get(f"{A}/organizations/not-an-id", headers=staff).status_code == 422
    why = {"reason": "A good reason"}
    assert (
        api.post(f"{A}/organizations/{nobody}/suspend", json=why, headers=staff).status_code == 404
    )
    assert api.post(f"{A}/users/{nobody}/disable", json=why, headers=staff).status_code == 404


def test_suspending_a_business_locks_its_people_out_until_it_is_reactivated(
    api, db, signup, business
):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    assert api.get(f"{ORGS}/{org}/billing", headers=auth["owner"]).status_code == 200
    res = api.post(
        f"{A}/organizations/{org}/suspend",
        json={"reason": "Unpaid invoice, as agreed"},
        headers=staff,
    )
    assert res.status_code == 204
    locked = api.get(f"{ORGS}/{org}/billing", headers=auth["owner"])
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "organization_suspended"
    assert (
        api.get(f"{ORGS}/{other}/billing", headers=auth["other"]).status_code == 200
    )  # nobody else is touched
    [entry] = audit_actions(db, "admin.organization_suspended")
    assert str(entry.organization_id) == org and entry.details == {
        "reason": "Unpaid invoice, as agreed"
    }
    again = api.post(
        f"{A}/organizations/{org}/suspend", json={"reason": "Once more please"}, headers=staff
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_active"
    listing = api.get(f"{A}/organizations?status=suspended", headers=staff).json()
    assert [r["name"] for r in listing["items"]] == ["Acme"]
    assert (
        api.post(
            f"{A}/organizations/{org}/reactivate", json={"reason": "Paid up now"}, headers=staff
        ).status_code
        == 204
    )
    assert api.get(f"{ORGS}/{org}/billing", headers=auth["owner"]).status_code == 200
    assert [e.details for e in audit_actions(db, "admin.organization_reactivated")] == [
        {"reason": "Paid up now"}
    ]
    nope = api.post(
        f"{A}/organizations/{org}/reactivate", json={"reason": "Not needed now"}, headers=staff
    )
    assert nope.status_code == 409 and nope.json()["error"]["code"] == "not_suspended"


def test_a_closed_business_cannot_be_suspended_or_reactivated_from_here(api, db, signup, business):
    staff = make_staff(api, db, signup)
    db.execute(
        update(Organization)
        .where(Organization.id == uuid.UUID(business[0]))
        .values(status="closed")
    )
    why = {"reason": "A good reason"}
    assert (
        api.post(f"{A}/organizations/{business[0]}/suspend", json=why, headers=staff).status_code
        == 409
    )
    assert (
        api.post(f"{A}/organizations/{business[0]}/reactivate", json=why, headers=staff).status_code
        == 409
    )


# --- people ---------------------------------------------------------------------------------------------


def test_people_can_be_found_by_name_or_email_with_how_many_businesses_they_are_in(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    rows = {r["email"]: r for r in api.get(f"{A}/users", headers=staff).json()["items"]}
    assert {
        "owner@acme.co.uk",
        "viewer@acme.co.uk",
        "owner@rival.co.uk",
        "staff@vyterlix.com",
    } <= set(rows)
    assert (
        rows["owner@acme.co.uk"]["organization_count"] == 1
        and rows["staff@vyterlix.com"]["organization_count"] == 0
    )
    assert (
        rows["staff@vyterlix.com"]["staff_role"] == "admin"
        and rows["owner@acme.co.uk"]["staff_role"] is None
    )
    assert (
        rows["owner@acme.co.uk"]["email_verified"] is True
        and rows["owner@acme.co.uk"]["is_active"] is True
    )
    assert set(rows["owner@acme.co.uk"]) == {
        "id",
        "email",
        "full_name",
        "is_active",
        "email_verified",
        "last_login_at",
        "created_at",
        "organization_count",
        "staff_role",
    }
    found = lambda q: [
        r["email"] for r in api.get(f"{A}/users?q={q}", headers=staff).json()["items"]
    ]  # noqa: E731
    assert (
        found("RIVAL") == ["owner@rival.co.uk"]
        and found("viewer") == ["viewer@acme.co.uk"]
        and found("%25") == []
    )
    assert api.get(f"{A}/users?limit=1", headers=staff).json()["total"] >= 4


def test_one_person_shows_their_businesses_and_sessions_but_no_secrets(api, db, signup, business):
    staff = make_staff(api, db, signup)
    owner_id = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    res = api.get(f"{A}/users/{owner_id}", headers=staff)
    assert res.status_code == 200
    body = res.json()
    assert body["memberships"] == [
        {
            "organization_id": business[0],
            "organization_name": "Acme",
            "organization_status": "active",
            "role": "owner",
            "status": "active",
        }
    ]
    assert body["active_sessions"] == 1
    assert "password" not in res.text and "hash" not in res.text.lower()


def test_locking_an_account_ends_every_session_and_blocks_login_until_it_is_unlocked(
    api, db, signup, business
):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    viewer_id = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    assert api.get(f"{ORGS}/{org}/billing", headers=auth["viewer"]).status_code == 200
    res = api.post(
        f"{A}/users/{viewer_id}/disable", json={"reason": "Reported as compromised"}, headers=staff
    )
    assert res.status_code == 204
    assert (
        api.get(f"{ORGS}/{org}/billing", headers=auth["viewer"]).status_code == 401
    )  # the open session ended
    assert (
        api.post(
            "/api/v1/auth/login", json={"email": "viewer@acme.co.uk", "password": TEST_PASSWORD}
        ).status_code
        == 403
    )
    [entry] = audit_actions(db, "admin.user_disabled")
    assert entry.details == {"reason": "Reported as compromised", "sessions_ended": 1}
    twice = api.post(
        f"{A}/users/{viewer_id}/disable", json={"reason": "Locking it again"}, headers=staff
    )
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "already_disabled"
    assert (
        api.post(
            f"{A}/users/{viewer_id}/enable", json={"reason": "False alarm, checked"}, headers=staff
        ).status_code
        == 204
    )
    assert (
        api.post(
            "/api/v1/auth/login", json={"email": "viewer@acme.co.uk", "password": TEST_PASSWORD}
        ).status_code
        == 200
    )
    assert [e.details for e in audit_actions(db, "admin.user_enabled")] == [
        {"reason": "False alarm, checked"}
    ]
    again = api.post(
        f"{A}/users/{viewer_id}/enable", json={"reason": "Unlocking it twice"}, headers=staff
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_disabled"
    detail = api.get(f"{A}/users/{viewer_id}", headers=staff).json()
    assert detail["is_active"] is True


def test_staff_cannot_lock_staff_or_themselves(api, db, signup, business):
    staff = make_staff(api, db, signup)
    other = make_staff(api, db, signup, "second@vyterlix.com", "support")
    mine = db.scalars(select(User.id).where(User.email == "staff@vyterlix.com")).one()
    theirs = db.scalars(select(User.id).where(User.email == "second@vyterlix.com")).one()
    why = {"reason": "Trying it out here"}
    for target in (mine, theirs):
        res = api.post(f"{A}/users/{target}/disable", json=why, headers=staff)
        assert res.status_code == 409 and res.json()["error"]["code"] == "staff_account"
    assert api.get(f"{A}/me", headers=other).json()["is_staff"] is True


def test_staff_can_change_someones_role_by_the_same_rules_as_the_owner_would(
    api, db, signup, business
):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    viewer_id = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    owner_id = db.scalars(select(User.id).where(User.email == "owner@acme.co.uk")).one()
    why = "Owner asked us by phone"
    res = api.put(
        f"{A}/organizations/{org}/members/{viewer_id}/role",
        json={"role": "manager", "reason": why},
        headers=staff,
    )
    assert res.status_code == 204
    members = {
        m["email"]: m["role"]
        for m in api.get(f"{ORGS}/{org}/members", headers=auth["owner"]).json()
    }
    assert members["viewer@acme.co.uk"] == "manager"
    [entry] = audit_actions(db, "admin.member_role_changed")
    assert str(entry.organization_id) == org and entry.details == {"role": "manager", "reason": why}
    assert "member.updated" in [
        e.action
        for e in db.scalars(select(AuditLog).where(AuditLog.organization_id == uuid.UUID(org)))
    ]
    last = api.put(
        f"{A}/organizations/{org}/members/{owner_id}/role",
        json={"role": "viewer", "reason": why},
        headers=staff,
    )
    assert last.status_code == 409 and last.json()["error"]["code"] == "last_owner"
    assert (
        api.put(
            f"{A}/organizations/{org}/members/{viewer_id}/role",
            json={"role": "boss", "reason": why},
            headers=staff,
        ).status_code
        == 422
    )
    stranger = db.scalars(select(User.id).where(User.email == "owner@rival.co.uk")).one()
    assert (
        api.put(
            f"{A}/organizations/{org}/members/{stranger}/role",
            json={"role": "viewer", "reason": why},
            headers=staff,
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"{A}/organizations/{uuid.uuid4()}/members/{viewer_id}/role",
            json={"role": "viewer", "reason": why},
            headers=staff,
        ).status_code
        == 404
    )


# --- trials ----------------------------------------------------------------------------------------------------


def test_a_trial_can_be_extended_and_one_that_has_ended_is_brought_back(api, db, signup, business):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    api.get(f"{ORGS}/{org}/billing", headers=auth["owner"])
    with scoped(db, business):
        before = db.scalars(select(Subscription.trial_ends_at)).one()
    res = api.post(
        f"{A}/organizations/{org}/extend-trial",
        json={"days": 10, "reason": "Needs longer to decide"},
        headers=staff,
    )
    assert res.status_code == 200, res.text
    assert datetime.fromisoformat(res.json()["trial_ends_at"]) == before + timedelta(days=10)
    with scoped(db, business):
        db.execute(update(Subscription).values(trial_ends_at=datetime.now(UTC) - timedelta(days=3)))
    assert (
        api.get(f"{ORGS}/{org}/billing", headers=auth["owner"]).json()["subscription"]["status"]
        == "expired"
    )
    res = api.post(
        f"{A}/organizations/{org}/extend-trial",
        json={"days": 7, "reason": "Gave it another go"},
        headers=staff,
    )
    assert res.json()["status"] == "trialing"
    now_billing = api.get(f"{ORGS}/{org}/billing", headers=auth["owner"]).json()["subscription"]
    assert now_billing["status"] == "trialing" and now_billing["trial_days_left"] == 7
    assert [e.details["days"] for e in audit_actions(db, "admin.trial_extended")] == [10, 7]


def test_a_business_that_already_pays_has_no_trial_to_extend_and_the_days_are_limited(
    api, db, signup, business
):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    from app.api.v1.billing import get_payment_providers
    from tests.test_billing import SANDBOX, pay

    api.app.dependency_overrides[get_payment_providers] = lambda: {"sandbox": SANDBOX}
    try:
        pay(api, business, "growth")
    finally:
        api.app.dependency_overrides.pop(get_payment_providers, None)
    res = api.post(
        f"{A}/organizations/{org}/extend-trial",
        json={"days": 5, "reason": "Should not work"},
        headers=staff,
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_on_trial"
    for days in (0, 91, -1):
        assert (
            api.post(
                f"{A}/organizations/{org}/extend-trial",
                json={"days": days, "reason": "Out of range"},
                headers=staff,
            ).status_code
            == 422
        )


# --- the audit trail -----------------------------------------------------------------------------------------------


def test_the_audit_trail_spans_every_business_and_can_be_narrowed(api, db, signup, business):
    org, other, auth = business
    staff = make_staff(api, db, signup)
    api.post(
        f"{ORGS}/{org}/invitations",
        json={"email": "n@acme.co.uk", "role": "viewer"},
        headers=auth["owner"],
    )
    api.get(f"{A}/organizations/{org}", headers=staff)
    everything = api.get(f"{A}/audit-log?limit=100", headers=staff).json()["entries"]
    orgs_seen = {e["organization_name"] for e in everything}
    assert {"Acme", "Rival", None} <= orgs_seen
    one = api.get(f"{A}/audit-log?organization_id={other}&limit=100", headers=staff).json()[
        "entries"
    ]
    assert one and {e["organization_name"] for e in one} == {"Rival"}
    mine = api.get(f"{A}/audit-log?actor_email=STAFF@vyterlix.com", headers=staff).json()["entries"]
    assert mine and {e["actor_email"] for e in mine} == {"staff@vyterlix.com"}
    only = api.get(f"{A}/audit-log?action=invitation.created", headers=staff).json()["entries"]
    assert [e["action"] for e in only] == ["invitation.created"] and only[0][
        "organization_name"
    ] == "Acme"
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (
        api.get(f"{A}/audit-log", params={"since": future}, headers=staff).json()["entries"] == []
    )
    assert (
        api.get(f"{A}/audit-log", params={"until": future, "limit": 1}, headers=staff).json()[
            "entries"
        ]
        != []
    )
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert api.get(f"{A}/audit-log", params={"until": past}, headers=staff).json()["entries"] == []


def test_the_audit_trail_is_read_a_page_at_a_time_without_repeats(api, db, signup, business):
    staff = make_staff(api, db, signup)
    first = api.get(f"{A}/audit-log?limit=3", headers=staff).json()
    assert len(first["entries"]) == 3 and first["next_before"] == first["entries"][-1]["id"]
    second = api.get(f"{A}/audit-log?limit=3&before={first['next_before']}", headers=staff).json()
    ids = [e["id"] for e in first["entries"] + second["entries"]]
    assert len(set(ids)) == len(ids) == 6
    stamps = [e["created_at"] for e in first["entries"] + second["entries"]]
    assert stamps == sorted(stamps, reverse=True)
    everything = api.get(f"{A}/audit-log?limit=100", headers=staff).json()
    assert everything["next_before"] is None
    assert api.get(f"{A}/audit-log?before={uuid.uuid4()}", headers=staff).status_code == 404


# --- plans -----------------------------------------------------------------------------------------------------------------


def test_staff_see_every_plan_including_ones_hidden_from_businesses(api, db, signup, business):
    staff = make_staff(api, db, signup)
    db.execute(update(Plan).where(Plan.code == "starter").values(is_public=False, is_active=False))
    seen = {p["code"]: p for p in api.get(f"{A}/plans", headers=staff).json()}
    assert (
        list(seen) == ["starter", "growth", "scale", "corporate"]
        and seen["starter"]["is_active"] is False
    )
    assert seen["growth"]["features"] == [
        {"feature": "members", "enabled": True, "limit": 10},
        {"feature": "integrations", "enabled": True, "limit": 3},
        {"feature": "scheduled_reports", "enabled": True, "limit": 5},
        {"feature": "ai_assistant", "enabled": True, "limit": None},
    ]
    shown = [
        p["code"]
        for p in api.get(f"{ORGS}/{business[0]}/billing/plans", headers=business[2]["owner"]).json()
    ]
    assert shown == ["growth", "scale", "corporate"]


def test_changing_a_price_changes_what_is_offered_but_not_what_a_paying_business_is_charged(
    api, db, signup, business
):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    from app.api.v1.billing import get_payment_providers
    from tests.test_billing import SANDBOX, pay, subscription

    api.app.dependency_overrides[get_payment_providers] = lambda: {"sandbox": SANDBOX}
    try:
        pay(api, business, "growth")
        before = subscription(db, business).current_period_end
        res = api.patch(
            f"{A}/plans/growth",
            json={
                "price_month_pence": 24900,
                "price_year_pence": 249000,
                "reason": "Price rise from next year",
            },
            headers=staff,
        )
        assert res.status_code == 200 and res.json()["price_month_pence"] == 24900
        offered = {
            p["code"]: p
            for p in api.get(f"{ORGS}/{org}/billing/plans", headers=auth["owner"]).json()
        }
        assert (
            offered["growth"]["price_month"] == "£249.00"
            and offered["growth"]["price_year"] == "£2,490.00"
        )
        assert subscription(db, business).current_period_end == before
        assert (
            api.get(f"{ORGS}/{org}/billing", headers=auth["owner"]).json()["subscription"][
                "plan_code"
            ]
            == "growth"
        )
    finally:
        api.app.dependency_overrides.pop(get_payment_providers, None)
    [entry] = audit_actions(db, "admin.plan_changed")
    assert entry.target_id == "growth" and entry.organization_id is None
    assert entry.details["from"] == {"price_month_pence": 19900, "price_year_pence": 199000}
    assert entry.details["to"] == {"price_month_pence": 24900, "price_year_pence": 249000}


def test_a_plan_can_be_hidden_and_what_it_includes_changed(api, db, signup, business):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    hide = api.patch(
        f"{A}/plans/starter", json={"is_public": False, "reason": "Retired for now"}, headers=staff
    )
    assert hide.status_code == 200 and hide.json()["is_public"] is False
    res = api.put(
        f"{A}/plans/scale/features/members",
        json={"enabled": True, "limit": 2, "reason": "Tightening for a test"},
        headers=staff,
    )
    assert res.status_code == 200
    assert [f for f in res.json()["features"] if f["feature"] == "members"] == [
        {"feature": "members", "enabled": True, "limit": 2}
    ]
    refused = api.post(
        f"{ORGS}/{org}/invitations",
        json={"email": "extra@acme.co.uk", "role": "viewer"},
        headers=auth["owner"],
    )
    assert refused.status_code == 402  # the trial's plan now holds two people, and Acme has two
    entry = audit_actions(db, "admin.plan_changed")[-1]
    assert entry.details["feature"] == "members" and entry.details["from"] == {
        "enabled": True,
        "limit": 25,
    }
    assert entry.details["to"] == {"enabled": True, "limit": 2}


def test_a_bad_plan_change_is_refused(api, db, signup, business):
    staff = make_staff(api, db, signup)
    why = "Some good reason"
    assert (
        api.patch(
            f"{A}/plans/nothing", json={"price_month_pence": 1, "reason": why}, headers=staff
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"{A}/plans/nothing/features/members",
            json={"enabled": True, "reason": why},
            headers=staff,
        ).status_code
        == 404
    )
    assert (
        api.put(
            f"{A}/plans/growth/features/everything",
            json={"enabled": True, "reason": why},
            headers=staff,
        ).status_code
        == 422
    )
    assert (
        api.patch(
            f"{A}/plans/growth", json={"price_month_pence": -1, "reason": why}, headers=staff
        ).status_code
        == 422
    )
    assert (
        api.patch(f"{A}/plans/growth", json={"price_month_pence": 5}, headers=staff).status_code
        == 422
    )
    assert (
        api.patch(
            f"{A}/plans/growth", json={"colour": "red", "reason": why}, headers=staff
        ).status_code
        == 422
    )
    assert (
        api.put(
            f"{A}/plans/growth/features/members",
            json={"enabled": True, "limit": -1, "reason": why},
            headers=staff,
        ).status_code
        == 422
    )
    assert db.scalars(select(Plan.price_month_pence).where(Plan.code == "growth")).one() == 19900


# --- who is staff ------------------------------------------------------------------------------------------------------------


def test_staff_rights_are_given_and_taken_away_from_the_command_line_and_recorded(
    db, signup, capsys, monkeypatch, api
):
    from app.cli import staff as cli

    signup("new.staff@vyterlix.com")

    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "get_sessionmaker", lambda: lambda: Session())
    assert cli.main(["list"]) == 0 and "No platform staff yet." in capsys.readouterr().out
    assert cli.main(["grant", "NEW.staff@vyterlix.com", "--role", "support"]) == 0
    assert "is now platform staff (support)" in capsys.readouterr().out
    assert cli.main(["grant", "new.staff@vyterlix.com", "--role", "admin"]) == 0
    assert cli.main(["list"]) == 0 and "new.staff@vyterlix.com" in capsys.readouterr().out
    rows = db.scalars(select(PlatformStaff)).all()
    assert [(r.role, r.is_active) for r in rows] == [("admin", True)]
    assert cli.main(["revoke", "new.staff@vyterlix.com"]) == 0
    assert db.scalars(select(PlatformStaff.is_active)).one() is False
    assert (
        cli.main(["grant", "new.staff@vyterlix.com", "--role", "support"]) == 0
    )  # and can be given back
    assert db.scalars(select(PlatformStaff.is_active)).one() is True
    assert [e.details for e in audit_actions(db, "admin.staff_granted")] == [
        {"role": "support"},
        {"role": "admin"},
        {"role": "support"},
    ]
    assert len(audit_actions(db, "admin.staff_revoked")) == 1
    assert cli.main(["grant", "nobody@nowhere.example", "--role", "admin"]) == 1
    assert "No account has that email address" in capsys.readouterr().err
    assert cli.main(["revoke", "nobody@nowhere.example"]) == 1
    assert "not platform staff" in capsys.readouterr().err
    assert cli.main(["revoke", "new.staff@vyterlix.com"]) == 0
    assert cli.main(["revoke", "new.staff@vyterlix.com"]) == 1  # already revoked


def test_a_staff_row_must_have_a_known_role_and_a_person_is_staff_once(db, signup, api):
    signup("one@vyterlix.com")
    user = db.scalars(select(User.id).where(User.email == "one@vyterlix.com")).one()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PlatformStaff(user_id=user, role="boss"))
        db.flush()
    db.add(PlatformStaff(user_id=user, role="admin"))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PlatformStaff(user_id=user, role="support"))
        db.flush()


def test_looking_across_businesses_does_not_leave_the_next_request_unscoped(
    api, db, signup, business
):
    org, _, auth = business
    staff = make_staff(api, db, signup)
    api.get(f"{A}/organizations/{org}", headers=staff)
    api.get(f"{A}/organizations", headers=staff)
    assert api.get(f"{ORGS}/{org}/members", headers=auth["owner"]).status_code == 200
    assert api.get(f"{ORGS}/{org}/members", headers=auth["other"]).status_code in (
        403,
        404,
    )  # still kept apart


def test_a_suspended_membership_is_not_counted_among_a_businesss_people(api, db, signup, business):
    staff = make_staff(api, db, signup)
    with scoped(db, business):
        db.execute(
            update(OrganizationUser)
            .where(
                OrganizationUser.user_id
                == db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
            )
            .values(status="suspended")
        )
    rows = {r["name"]: r for r in api.get(f"{A}/organizations", headers=staff).json()["items"]}
    assert rows["Acme"]["member_count"] == 1


def test_a_time_filter_includes_the_very_moment_it_names_and_a_page_that_ends_exactly_has_no_next(
    api, db, signup, business
):
    staff = make_staff(api, db, signup)
    everything = api.get(f"{A}/audit-log?limit=100", headers=staff).json()
    assert everything["next_before"] is None
    entries = everything["entries"]
    oldest = entries[-1]
    got = api.get(
        f"{A}/audit-log", params={"since": oldest["created_at"], "limit": 100}, headers=staff
    ).json()["entries"]
    assert oldest["id"] in [e["id"] for e in got]
    exact = api.get(f"{A}/audit-log?limit={len(entries)}", headers=staff).json()
    assert len(exact["entries"]) == len(entries) and exact["next_before"] is None
    one_short = api.get(f"{A}/audit-log?limit={len(entries) - 1}", headers=staff).json()
    assert one_short["next_before"] == one_short["entries"][-1]["id"]
