import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.security import hash_token
from app.models.identity import AuditLog, LoginAttempt, User, UserSession, UserToken
from app.services.email import EmailMessage

EMAIL = "owner@acme.co.uk"
OLD_PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new passphrase"
GENERIC = "If an account exists for that email, we've sent a link to reset the password."


@pytest.fixture
def user(api, db, outbox):
    res = api.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": OLD_PASSWORD, "full_name": "Ada Owner"},
    )
    assert res.status_code == 201
    outbox.clear()  # drop the verification email; these tests only look at reset emails
    return db.get(User, uuid.UUID(res.json()["id"]))


def forgot(api, email=EMAIL):
    return api.post("/api/v1/auth/forgot-password", json={"email": email})


def reset(api, token, password=NEW_PASSWORD):
    return api.post("/api/v1/auth/reset-password", json={"token": token, "new_password": password})


def login(api, password):
    return api.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


def reset_token(message: EmailMessage) -> str:
    match = re.search(r"reset-password\.html\?token=([A-Za-z0-9_\-]+)", message.body)
    assert match, message.body
    return match.group(1)


def age_tokens(db, seconds: int):
    db.execute(
        update(UserToken).values(created_at=UserToken.created_at - timedelta(seconds=seconds))
    )


# --- forgot password ------------------------------------------------------------


def test_forgot_password_emails_a_reset_link(api, db, user, outbox):
    res = forgot(api, "Owner@ACME.co.uk")  # normalised like login
    assert res.status_code == 202
    assert res.json() == {"message": GENERIC}

    assert len(outbox) == 1
    msg = outbox[0]
    assert msg.to == EMAIL
    assert "Reset your Vyterlix password" in msg.subject
    assert "60 minutes" in msg.body

    stored = db.scalars(select(UserToken).where(UserToken.purpose == "password_reset")).one()
    assert stored.token_hash == hash_token(reset_token(msg))
    expires_in = stored.expires_at - datetime.now(UTC)
    assert timedelta(minutes=59) < expires_in <= timedelta(minutes=60)


def test_unknown_email_gets_the_same_answer_and_no_email(api, outbox):
    res = forgot(api, "nobody@acme.co.uk")
    assert res.status_code == 202
    assert res.json() == {"message": GENERIC}
    assert outbox == []


def test_disabled_account_gets_no_email(api, db, user, outbox):
    db.execute(update(User).values(is_active=False))
    assert forgot(api).status_code == 202
    assert outbox == []


def test_repeat_request_within_cooldown_sends_nothing(api, user, outbox):
    forgot(api)
    forgot(api)
    assert len(outbox) == 1


def test_new_request_cancels_the_previous_link(api, db, user, outbox):
    forgot(api)
    age_tokens(db, 120)
    forgot(api)
    first, second = reset_token(outbox[0]), reset_token(outbox[1])
    assert reset(api, first).status_code == 400
    assert reset(api, second).status_code == 200


def test_request_is_audited(api, db, user):
    forgot(api)
    actions = db.scalars(select(AuditLog.action)).all()
    assert "auth.password_reset_requested" in actions


# --- reset password -----------------------------------------------------------------


def test_reset_changes_the_password(api, db, user, outbox):
    forgot(api)
    res = reset(api, reset_token(outbox[0]))
    assert res.status_code == 200
    assert "reset" in res.json()["message"].lower()

    assert login(api, OLD_PASSWORD).status_code == 401
    assert login(api, NEW_PASSWORD).status_code == 200
    db.refresh(user)
    assert user.password_changed_at is not None


def test_reset_link_is_single_use(api, user, outbox):
    forgot(api)
    token = reset_token(outbox[0])
    assert reset(api, token).status_code == 200
    again = reset(api, token, "yet another passphrase")
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"


def test_expired_link_rejected(api, db, user, outbox):
    forgot(api)
    db.execute(update(UserToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert reset(api, reset_token(outbox[0])).status_code == 400
    assert login(api, OLD_PASSWORD).status_code == 200  # password unchanged


def test_email_verification_token_cannot_reset_password(api, db, user, outbox):
    forgot(api)
    db.execute(update(UserToken).values(purpose="email_verification"))
    assert reset(api, reset_token(outbox[0])).status_code == 400


def test_disabled_account_cannot_use_an_existing_link(api, db, user, outbox):
    forgot(api)
    db.execute(update(User).values(is_active=False))
    assert reset(api, reset_token(outbox[0])).status_code == 400


def test_reset_logs_out_every_device(app, api, db, user, outbox):
    from fastapi.testclient import TestClient

    phone = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    laptop_token = login(api, OLD_PASSWORD).json()["access_token"]
    phone_token = login(phone, OLD_PASSWORD).json()["access_token"]

    forgot(api)
    res = reset(api, reset_token(outbox[0]))
    assert 'vyterlix_refresh=""' in res.headers["set-cookie"]

    for client, token in ((api, laptop_token), (phone, phone_token)):
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["error"]["code"] == "session_ended"
    assert phone.post("/api/v1/auth/refresh").status_code == 401
    assert (
        db.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.revoked_at.is_(None))
        )
        == 0
    )


def test_reset_lifts_the_login_lockout(api, user, outbox):
    for _ in range(5):
        login(api, "wrong password!")
    assert login(api, OLD_PASSWORD).status_code == 429

    forgot(api)
    reset(api, reset_token(outbox[0]))
    assert login(api, NEW_PASSWORD).status_code == 200


def test_reset_marks_an_unverified_email_as_verified(api, db, user, outbox):
    assert user.email_verified_at is None
    forgot(api)
    reset(api, reset_token(outbox[0]))
    db.refresh(user)
    assert user.email_verified_at is not None


def test_reset_sends_a_password_changed_alert(api, user, outbox):
    forgot(api)
    reset(api, reset_token(outbox[0]))
    assert len(outbox) == 2
    alert = outbox[1]
    assert alert.to == EMAIL
    assert "password was changed" in alert.subject
    assert "logged out on all devices" in alert.body
    assert NEW_PASSWORD not in alert.body


def test_reset_is_audited_without_the_password(api, db, user, outbox):
    forgot(api)
    reset(api, reset_token(outbox[0]))
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "auth.password_reset")).one()
    assert entry.actor_user_id == user.id
    assert entry.details == {"sessions_revoked": 0}
    assert NEW_PASSWORD not in str(entry.details)


# --- new-password rules ---------------------------------------------------------------


@pytest.mark.parametrize("password", ["too short", "x" * 129])
def test_new_password_follows_registration_rules(api, user, outbox, password):
    forgot(api)
    res = reset(api, reset_token(outbox[0]), password)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_rejected_password_does_not_burn_the_link(api, user, outbox):
    forgot(api)
    token = reset_token(outbox[0])

    same_as_email = reset(api, token, "Owner@Acme.co.uk")
    assert same_as_email.status_code == 422
    assert same_as_email.json()["error"]["code"] == "password_same_as_email"

    assert reset(api, token).status_code == 200  # same link still works with a good password


def test_rejected_password_changes_nothing(api, db, user, outbox):
    forgot(api)
    reset(api, reset_token(outbox[0]), "owner@acme.co.uk")
    assert login(api, OLD_PASSWORD).status_code == 200
    assert (
        db.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))
        )
        == 0
    )
