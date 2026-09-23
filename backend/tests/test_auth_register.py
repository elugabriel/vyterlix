import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import verify_password
from app.models.identity import AuditLog, User

URL = "/api/v1/auth/register"
GOOD_PASSWORD = "correct horse battery"


def register(api, **overrides):
    body = {"email": "owner@acme.co.uk", "password": GOOD_PASSWORD, "full_name": "Ada Owner"}
    body.update(overrides)
    return api.post(URL, json=body, headers={"User-Agent": "pytest-agent"})


def test_register_creates_unverified_user(api, db):
    res = register(api)
    assert res.status_code == 201
    data = res.json()
    assert data["email"] == "owner@acme.co.uk"
    assert data["full_name"] == "Ada Owner"
    assert data["email_verified"] is False
    assert set(data) == {"id", "email", "full_name", "email_verified", "created_at"}

    user = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    assert str(user.id) == data["id"]
    assert user.email_verified_at is None


def test_password_is_stored_as_argon2_hash(api, db):
    register(api)
    user = db.scalars(select(User)).one()
    assert user.password_hash.startswith("$argon2id$")
    assert GOOD_PASSWORD not in user.password_hash
    assert verify_password(user.password_hash, GOOD_PASSWORD)
    assert not verify_password(user.password_hash, "wrong password!!")


def test_response_never_contains_password_or_hash(api):
    body = register(api).text
    assert GOOD_PASSWORD not in body
    assert "argon2" not in body
    assert "password" not in body


def test_email_is_lowercased_and_trimmed(api):
    res = register(api, email="  Owner@ACME.co.uk ")
    assert res.status_code == 201
    assert res.json()["email"] == "owner@acme.co.uk"


def test_full_name_is_trimmed(api):
    assert register(api, full_name="  Ada Owner  ").json()["full_name"] == "Ada Owner"


def test_duplicate_email_returns_409(api):
    assert register(api).status_code == 201
    res = register(api, email="OWNER@acme.co.uk", full_name="Someone Else")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"password": "short pass"}, "password"),  # < 12 chars
        ({"password": "x" * 129}, "password"),  # > 128 chars
        ({"email": "not-an-email"}, "email"),
        ({"full_name": "   "}, "full_name"),
    ],
)
def test_invalid_input_rejected(api, overrides, field):
    res = register(api, **overrides)
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "validation_error"
    assert any(field in d["loc"] for d in err["details"])


def test_password_equal_to_email_rejected(api):
    res = register(api, email="longaddress@acme.co.uk", password="LongAddress@acme.co.uk")
    assert res.status_code == 422


def test_unknown_fields_rejected(api):
    # Stops clients trying to set e.g. email_verified or is_active on sign-up.
    res = register(api, email_verified=True)
    assert res.status_code == 422


def test_registration_is_audited_without_secrets(app, api, db):
    client = TestClient(app, client=("203.0.113.9", 50000))  # `api` already bound the test DB
    user_id = register(client).json()["id"]
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.registered")).one()
    assert str(entry.actor_user_id) == user_id
    assert entry.target_type == "user"
    assert entry.user_agent == "pytest-agent"
    assert str(entry.ip_address) == "203.0.113.9"
    assert entry.details is None


def test_non_ip_client_address_is_not_stored(api, db):
    # The default test client reports its host as "testclient", like a unix socket would.
    assert register(api).status_code == 201
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "user.registered")).one()
    assert entry.ip_address is None
