import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.services.audit import AuditAction, record_audit

ORGS = "/api/v1/organizations"
PASSWORD = "correct horse battery"


def _user_id(db, email):
    return db.scalars(select(User.id).where(User.email == email)).one()


def _actions(db, **where):
    stmt = select(AuditLog)
    for column, value in where.items():
        stmt = stmt.where(getattr(AuditLog, column) == value)
    return [e.action for e in db.scalars(stmt.order_by(AuditLog.created_at)).all()]


def _login(client, email, password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


# --- the action list itself -------------------------------------------------------------


def test_action_names_follow_the_convention():
    for action in AuditAction:
        assert re.fullmatch(r"[a-z]+\.[a-z_]+", action.value), action


def test_free_text_actions_are_refused(db):
    with pytest.raises(TypeError, match="Unknown audit action"):
        record_audit(db, "user.hacked")


# --- the three gaps closed in this step -------------------------------------------------


def test_blocked_login_is_audited(api, db, signup):
    signup("victim@acme.co.uk")
    for _ in range(5):
        _login(api, "victim@acme.co.uk", "wrong password!")
    assert _login(api, "victim@acme.co.uk").status_code == 429

    blocked = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.AUTH_LOGIN_BLOCKED)
    ).one()
    assert blocked.actor_user_id == _user_id(db, "victim@acme.co.uk")
    assert blocked.details == {"reason": "email_limit"}


def test_blocked_login_from_one_ip_across_many_emails_is_audited(app, api, db):
    attacker = TestClient(app, base_url="https://testserver", client=("198.51.100.9", 4000))
    for i in range(20):
        _login(attacker, f"guess{i}@acme.co.uk", "guess guess")
    assert _login(attacker, "another@acme.co.uk", "guess guess").status_code == 429

    blocked = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.AUTH_LOGIN_BLOCKED)
    ).one()
    assert blocked.details == {"reason": "ip_limit"}
    assert str(blocked.ip_address) == "198.51.100.9"
    assert blocked.actor_user_id is None  # unknown email


def test_login_to_a_disabled_account_is_audited(api, db, signup):
    signup("gone@acme.co.uk")
    db.execute(update(User).values(is_active=False))
    assert _login(api, "gone@acme.co.uk").status_code == 403
    entry = db.scalars(
        select(AuditLog).where(AuditLog.details["reason"].astext == "account_disabled")
    ).one()
    assert entry.action == AuditAction.AUTH_LOGIN_FAILED


def test_invitation_used_by_the_wrong_account_is_audited(api, db, signup, outbox):
    owner = signup("owner@acme.co.uk")
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=owner).json()["id"]
    api.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "invitee@acme.co.uk", "role": "viewer"},
        headers=owner,
    )
    token = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1].body).group(1)

    intruder = signup("intruder@acme.co.uk")
    api.post("/api/v1/invitations/accept", json={"token": token}, headers=intruder)

    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.INVITATION_ACCEPT_REJECTED)
    ).one()
    assert str(entry.organization_id) == org_id
    assert entry.actor_user_id == _user_id(db, "intruder@acme.co.uk")
    assert entry.details == {"reason": "email_mismatch"}


# --- append-only, enforced by the database ---------------------------------------------


@pytest.fixture
def entry(db):
    user = User(email="someone@acme.co.uk", password_hash="x", full_name="Someone")
    db.add(user)
    db.flush()
    record_audit(db, AuditAction.USER_REGISTERED, actor_user_id=user.id)
    db.flush()
    return db.scalars(select(AuditLog)).one()


def _in_savepoint(db, statement):
    """Run a statement that may fail without breaking the test's outer transaction."""
    with db.begin_nested():
        db.execute(statement)


def test_audit_rows_cannot_be_edited(db, entry):
    with pytest.raises(DBAPIError, match="append-only: rows cannot be edited"):
        _in_savepoint(db, update(AuditLog).values(action="user.innocent"))


def test_audit_rows_cannot_be_reassigned_to_someone_else(db, entry):
    other = User(email="other@acme.co.uk", password_hash="x", full_name="Other")
    db.add(other)
    db.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        _in_savepoint(db, update(AuditLog).values(actor_user_id=other.id))


def test_audit_rows_cannot_be_deleted(db, entry):
    with pytest.raises(DBAPIError, match="append-only: rows cannot be deleted"):
        _in_savepoint(db, delete(AuditLog))


def test_retention_cleanup_can_delete_when_it_opts_in(db, entry):
    with db.begin_nested():
        db.execute(text("SET LOCAL vyterlix.allow_audit_delete = 'on'"))
        db.execute(delete(AuditLog))
    assert db.scalars(select(AuditLog)).all() == []


def test_deleting_a_user_keeps_their_history_with_the_actor_blanked(db, entry):
    db.execute(delete(User).where(User.email == "someone@acme.co.uk"))
    db.expire_all()
    kept = db.scalars(select(AuditLog)).one()
    assert kept.action == AuditAction.USER_REGISTERED
    assert kept.actor_user_id is None


# --- the organisation's audit log endpoint ------------------------------------------------


@pytest.fixture
def business(api, db, signup, outbox):
    """Acme (owner, manager, viewer) with some history, plus an unrelated Rival Ltd."""
    owner = signup("owner@acme.co.uk")
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=owner).json()["id"]
    auth = {"owner": owner}
    for who in ("manager", "viewer"):
        auth[who] = signup(f"{who}@acme.co.uk")
        role_id = db.scalars(select(Role.id).where(Role.code == who)).first()
        db.add(
            OrganizationUser(
                organization_id=uuid.UUID(org_id),
                user_id=_user_id(db, f"{who}@acme.co.uk"),
                role_id=role_id,
            )
        )
    db.flush()
    api.patch(f"{ORGS}/{org_id}", json={"name": "Acme Trading"}, headers=owner)
    api.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "new@acme.co.uk", "role": "viewer"},
        headers=owner,
    )
    rival = signup("rival@acme.co.uk")
    api.post(ORGS, json={"name": "Rival Ltd"}, headers=rival)
    return org_id, auth


def audit_log(api, auth, org_id, **params):
    return api.get(f"{ORGS}/{org_id}/audit-log", headers=auth, params=params)


def test_owner_sees_the_business_history_newest_first(api, business):
    org_id, auth = business
    res = audit_log(api, auth["owner"], org_id)
    assert res.status_code == 200
    entries = res.json()["entries"]
    assert [e["action"] for e in entries] == [
        "invitation.created",
        "organization.updated",
        "organization.created",
    ]
    assert entries[0]["actor"]["email"] == "owner@acme.co.uk"
    assert entries[0]["details"] == {"email": "new@acme.co.uk", "role": "viewer"}
    assert res.json()["next_before"] is None


def test_account_level_events_are_not_in_the_business_log(api, business):
    org_id, auth = business
    actions = {e["action"] for e in audit_log(api, auth["owner"], org_id).json()["entries"]}
    assert not actions & {"auth.login_succeeded", "user.registered"}


def test_another_business_history_never_appears(api, db, business):
    org_id, auth = business
    rival_created = db.scalars(
        select(AuditLog.id).where(
            AuditLog.action == "organization.created", AuditLog.organization_id != org_id
        )
    ).one()
    ids = {e["id"] for e in audit_log(api, auth["owner"], org_id).json()["entries"]}
    assert str(rival_created) not in ids
    # Using the rival's entry as a cursor doesn't work either.
    res = audit_log(api, auth["owner"], org_id, before=str(rival_created))
    assert res.status_code == 404


@pytest.mark.parametrize("who", ["manager", "viewer"])
def test_only_owners_can_read_the_audit_log(api, business, who):
    org_id, auth = business
    res = audit_log(api, auth[who], org_id)
    assert res.status_code == 403
    assert res.json()["error"]["details"] == {"required_permission": "audit.view"}


def test_outsiders_get_404(api, business, signup):
    org_id, _ = business
    assert audit_log(api, signup("stranger@acme.co.uk"), org_id).status_code == 404


def test_pagination_walks_every_entry_once(api, business):
    org_id, auth = business
    seen, before = [], None
    while True:
        params = {"limit": 2} | ({"before": before} if before else {})
        page = audit_log(api, auth["owner"], org_id, **params).json()
        seen += [e["id"] for e in page["entries"]]
        before = page["next_before"]
        if before is None:
            break
    everything = [e["id"] for e in audit_log(api, auth["owner"], org_id).json()["entries"]]
    assert seen == everything
    assert len(seen) == len(set(seen)) == 3


def test_filter_by_action(api, business):
    org_id, auth = business
    entries = audit_log(api, auth["owner"], org_id, action="organization.updated").json()["entries"]
    assert [e["action"] for e in entries] == ["organization.updated"]


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"action": "made.up"}, {"before": "nope"}]
)
def test_bad_query_parameters_rejected(api, business, params):
    org_id, auth = business
    assert audit_log(api, auth["owner"], org_id, **params).status_code == 422
