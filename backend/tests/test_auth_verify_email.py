import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.config import Settings
from app.core.security import hash_token
from app.models.identity import AuditLog, User, UserToken
from app.services.email import ConsoleEmailSender, EmailMessage

PASSWORD = "correct horse battery"
EMAIL = "owner@acme.co.uk"


def register(api, email=EMAIL):
    res = api.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Ada Owner"},
    )
    assert res.status_code == 201
    return res.json()


def token_from(message: EmailMessage) -> str:
    match = re.search(r"verify-email\.html\?token=([A-Za-z0-9_\-]+)", message.body)
    assert match, message.body
    return match.group(1)


def verify(api, token):
    return api.post("/api/v1/auth/verify-email", json={"token": token})


def resend(api, email=EMAIL):
    return api.post("/api/v1/auth/resend-verification", json={"email": email})


def age_tokens(db, seconds: int):
    """Pretend every existing token was created `seconds` ago (to get past the cooldown)."""
    db.execute(
        update(UserToken).values(created_at=UserToken.created_at - timedelta(seconds=seconds))
    )


# --- sending on registration -------------------------------------------------


def test_registration_emails_a_verification_link(api, outbox):
    register(api)
    assert len(outbox) == 1
    msg = outbox[0]
    assert msg.to == EMAIL
    assert "Confirm your email" in msg.subject
    assert msg.body.count("http://localhost:5500/verify-email.html?token=") == 1
    assert "24 hours" in msg.body


def test_only_the_token_hash_is_stored(api, db, outbox):
    register(api)
    raw = token_from(outbox[0])
    stored = db.scalars(select(UserToken)).one()
    assert stored.purpose == "email_verification"
    assert stored.token_hash == hash_token(raw)
    assert raw not in stored.token_hash
    expires_in = stored.expires_at - datetime.now(UTC)
    assert timedelta(hours=23, minutes=59) < expires_in <= timedelta(hours=24)


# --- verifying ----------------------------------------------------------------


def test_valid_token_verifies_the_account(api, db, outbox):
    register(api)
    res = verify(api, token_from(outbox[0]))
    assert res.status_code == 200
    assert res.json()["email_verified"] is True
    user = db.scalars(select(User)).one()
    assert user.email_verified_at is not None


def test_token_is_single_use(api, outbox):
    register(api)
    token = token_from(outbox[0])
    assert verify(api, token).status_code == 200
    again = verify(api, token)
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"


def test_expired_token_rejected(api, db, outbox):
    register(api)
    db.execute(update(UserToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    res = verify(api, token_from(outbox[0]))
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_token"
    assert db.scalars(select(User)).one().email_verified_at is None


def test_unknown_token_rejected(api):
    res = verify(api, "x" * 43)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_token"


def test_password_reset_token_cannot_verify_email(api, db, outbox):
    register(api)
    db.execute(update(UserToken).values(purpose="password_reset"))
    assert verify(api, token_from(outbox[0])).status_code == 400


@pytest.mark.parametrize("token", ["", "short", "y" * 201])
def test_malformed_token_is_a_validation_error(api, token):
    assert verify(api, token).status_code == 422


def test_verification_is_audited(api, db, outbox):
    user_id = register(api)["id"]
    verify(api, token_from(outbox[0]))
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.email_verified")).one()
    assert str(entry.actor_user_id) == user_id


# --- resending ----------------------------------------------------------------

GENERIC = "If that account still needs verifying, we've sent a new link to it."


def test_resend_sends_new_link_and_cancels_the_old_one(api, db, outbox):
    register(api)
    old = token_from(outbox[0])
    age_tokens(db, 120)

    res = resend(api, "Owner@ACME.co.uk")  # email normalised like registration
    assert res.status_code == 202
    assert res.json() == {"message": GENERIC}
    assert len(outbox) == 2
    new = token_from(outbox[1])
    assert new != old

    assert verify(api, old).status_code == 400
    assert verify(api, new).status_code == 200


def test_resend_within_cooldown_sends_nothing(api, outbox):
    register(api)
    res = resend(api)
    assert res.status_code == 202
    assert res.json() == {"message": GENERIC}
    assert len(outbox) == 1  # only the registration email


def test_resend_for_unknown_email_looks_identical_and_sends_nothing(api, outbox):
    res = resend(api, "nobody@acme.co.uk")
    assert res.status_code == 202
    assert res.json() == {"message": GENERIC}
    assert outbox == []


def test_resend_for_verified_account_sends_nothing(api, db, outbox):
    register(api)
    verify(api, token_from(outbox[0]))
    age_tokens(db, 120)
    assert resend(api).status_code == 202
    assert len(outbox) == 1


def test_resend_for_deactivated_account_sends_nothing(api, db, outbox):
    register(api)
    db.execute(update(User).values(is_active=False))
    age_tokens(db, 120)
    assert resend(api).status_code == 202
    assert len(outbox) == 1


# --- email backend safety -----------------------------------------------------


def test_console_email_backend_is_refused_in_prod():
    with pytest.raises(ValidationError, match="Console email backend"):
        Settings(env="prod", jwt_secret="x" * 40, _env_file=None)


def test_console_sender_writes_the_email_to_the_log(caplog):
    with caplog.at_level(logging.INFO, logger="vyterlix.email"):
        ConsoleEmailSender().send(EmailMessage(to="a@acme.co.uk", subject="Hi", body="Link"))
    record = caplog.records[-1]
    assert record.message == "email.console"
    assert record.ctx["to"] == "a@acme.co.uk"
    assert record.ctx["body"] == "Link"
