import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.identity import AuditLog, LoginAttempt, User, UserSession

EMAIL = "owner@acme.co.uk"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def user(api, db, outbox):
    res = api.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "Ada Owner"},
    )
    assert res.status_code == 201
    outbox.clear()
    return db.get(User, uuid.UUID(res.json()["id"]))


def login(client, password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


@pytest.fixture
def auth(api, user):
    """Authorization header for a fresh login (unverified user — limited access)."""
    token = login(api).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def change(api, auth, current=PASSWORD, new=NEW_PASSWORD):
    return api.post(
        "/api/v1/me/change-password",
        json={"current_password": current, "new_password": new},
        headers=auth,
    )


def other_device(app):
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


# --- profile --------------------------------------------------------------------


def test_update_name(api, db, user, auth):
    res = api.patch("/api/v1/me", json={"full_name": "  Ada Lovelace  "}, headers=auth)
    assert res.status_code == 200
    assert res.json()["full_name"] == "Ada Lovelace"
    db.refresh(user)
    assert user.full_name == "Ada Lovelace"


def test_update_is_audited_with_field_names_only(api, db, auth):
    api.patch("/api/v1/me", json={"full_name": "Ada Lovelace"}, headers=auth)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.profile_updated")).one()
    assert entry.details == {"fields": ["full_name"]}


def test_no_op_update_is_not_audited(api, db, auth):
    res = api.patch("/api/v1/me", json={"full_name": "Ada Owner"}, headers=auth)
    assert res.status_code == 200
    assert "user.profile_updated" not in db.scalars(select(AuditLog.action)).all()


@pytest.mark.parametrize(
    "body",
    [
        {},  # nothing to change
        {"full_name": None},
        {"full_name": "   "},
        {"full_name": "x" * 201},
        {"email": "new@acme.co.uk"},  # email changes aren't allowed here
        {"email_verified": True},
        {"is_active": False},
    ],
)
def test_invalid_or_forbidden_profile_updates_rejected(api, auth, body):
    res = api.patch("/api/v1/me", json=body, headers=auth)
    assert res.status_code == 422


def test_profile_requires_login(api):
    assert api.patch("/api/v1/me", json={"full_name": "X"}).status_code == 401


# --- change password ------------------------------------------------------------


def test_change_password(api, db, user, auth):
    res = change(api, auth)
    assert res.status_code == 200
    assert login(api, PASSWORD).status_code == 401
    assert login(api, NEW_PASSWORD).status_code == 200
    db.refresh(user)
    assert user.password_changed_at is not None


def test_this_device_stays_logged_in_others_are_logged_out(app, api, auth):
    phone = other_device(app)
    phone_token = login(phone).json()["access_token"]

    assert change(api, auth).status_code == 200

    assert api.get("/api/v1/me", headers=auth).status_code == 200  # this device
    assert api.post("/api/v1/auth/refresh").status_code == 200  # its refresh cookie too
    me = phone.get("/api/v1/me", headers={"Authorization": f"Bearer {phone_token}"})
    assert me.json()["error"]["code"] == "session_ended"
    assert phone.post("/api/v1/auth/refresh").status_code == 401


def test_wrong_current_password_rejected_and_nothing_changes(api, db, auth):
    res = change(api, auth, current="not my password")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "incorrect_password"
    assert login(api, PASSWORD).status_code == 200
    assert "user.password_change_failed" in db.scalars(select(AuditLog.action)).all()


def test_wrong_current_password_counts_toward_login_lockout(api, db, auth):
    for _ in range(5):
        assert change(api, auth, current="not my password").status_code == 400
    blocked = change(api, auth)  # right password, but locked
    assert blocked.status_code == 429
    assert login(api).status_code == 429  # login is locked too
    failures = select(func.count()).select_from(LoginAttempt).where(~LoginAttempt.succeeded)
    assert db.scalar(failures) == 5


@pytest.mark.parametrize("new", ["too short", "x" * 129])
def test_new_password_follows_the_same_rules(api, auth, new):
    assert change(api, auth, new=new).status_code == 422


def test_new_password_cannot_be_the_email(api, auth):
    res = change(api, auth, new="OWNER@acme.co.uk")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "password_same_as_email"


def test_new_password_must_differ_from_current(api, auth):
    res = change(api, auth, new=PASSWORD)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "password_unchanged"


def test_change_cancels_outstanding_reset_link(api, auth, outbox):
    api.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    link = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1].body).group(1)

    assert change(api, auth).status_code == 200
    stale = api.post(
        "/api/v1/auth/reset-password",
        json={"token": link, "new_password": "attacker chosen pass"},
    )
    assert stale.status_code == 400
    assert login(api, NEW_PASSWORD).status_code == 200


def test_change_sends_alert_and_is_audited_without_passwords(app, api, db, auth, outbox):
    login(other_device(app))  # one other session to revoke
    change(api, auth)

    alert = outbox[-1]
    assert alert.to == EMAIL
    assert "password was changed" in alert.subject
    assert "logged out on your other devices" in alert.body

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.password_changed")).one()
    assert entry.details == {"other_sessions_revoked": 1}
    assert PASSWORD not in str(entry.details) and NEW_PASSWORD not in str(entry.details)


def test_change_password_requires_login(api):
    res = api.post(
        "/api/v1/me/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert res.status_code == 401


def test_live_session_count_after_change(app, api, db, auth):
    login(other_device(app))
    login(other_device(app))
    change(api, auth)
    live = select(func.count()).select_from(UserSession).where(UserSession.revoked_at.is_(None))
    assert db.scalar(live) == 1
