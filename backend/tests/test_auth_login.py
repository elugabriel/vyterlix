import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.api.deps import VerifiedUser
from app.core.config import DEV_JWT_SECRET, Settings, get_settings
from app.core.security import JWT_ALGORITHM, create_access_token, hash_token
from app.models.identity import AuditLog, LoginAttempt, User, UserSession

EMAIL = "owner@acme.co.uk"
PASSWORD = "correct horse battery"
COOKIE = "vyterlix_refresh"


@pytest.fixture
def user(api, db):
    res = api.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "Ada Owner"},
    )
    assert res.status_code == 201
    return db.get(User, uuid.UUID(res.json()["id"]))


def login(api, email=EMAIL, password=PASSWORD):
    return api.post("/api/v1/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- login ---------------------------------------------------------------------


def test_login_returns_access_token_and_sets_refresh_cookie(api, user):
    res = login(api)
    assert res.status_code == 200
    data = res.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 15 * 60
    assert data["user"]["email"] == EMAIL
    assert "refresh" not in str(data).lower()  # refresh token only ever travels in the cookie

    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie


def test_session_row_stores_only_the_refresh_hash(api, db, user):
    login(api)
    raw = api.cookies.get(COOKIE)
    session = db.scalars(select(UserSession)).one()
    assert session.refresh_token_hash == hash_token(raw)
    assert raw != session.refresh_token_hash
    assert session.revoked_at is None
    assert session.expires_at - datetime.now(UTC) > timedelta(days=29)


def test_login_is_case_insensitive_on_email(api, user):
    assert login(api, email="OWNER@Acme.co.uk").status_code == 200


def test_wrong_password_and_unknown_email_look_identical(api, user):
    wrong = login(api, password="not the password")
    unknown = login(api, email="nobody@acme.co.uk")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert COOKIE not in wrong.headers.get("set-cookie", "")


def test_unverified_user_can_log_in(api, user):
    assert user.email_verified_at is None
    res = login(api)
    assert res.status_code == 200
    assert res.json()["user"]["email_verified"] is False


def test_disabled_account_cannot_log_in(api, db, user):
    db.execute(update(User).values(is_active=False))
    res = login(api)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "account_disabled"


def test_login_updates_last_login_and_is_audited(api, db, user):
    login(api)
    db.refresh(user)
    assert user.last_login_at is not None
    actions = db.scalars(select(AuditLog.action)).all()
    assert "auth.login_succeeded" in actions


def test_failed_login_is_recorded_and_audited(api, db, user):
    login(api, password="not the password")
    attempt = db.scalars(select(LoginAttempt)).one()
    assert (attempt.email, attempt.succeeded) == (EMAIL, False)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).one()
    assert entry.actor_user_id == user.id
    assert PASSWORD not in str(entry.details) and "not the password" not in str(entry.details)


# --- rate limiting ------------------------------------------------------------


def test_too_many_failures_for_an_email_blocks_even_the_right_password(api, user):
    for _ in range(5):
        assert login(api, password="not the password").status_code == 401
    blocked = login(api)  # correct password, but locked for now
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_login_attempts"


def test_failures_older_than_the_window_do_not_count(api, db, user):
    for _ in range(5):
        login(api, password="not the password")
    db.execute(
        update(LoginAttempt).values(created_at=LoginAttempt.created_at - timedelta(minutes=16))
    )
    assert login(api).status_code == 200


def test_unknown_emails_are_rate_limited_too(api):
    for _ in range(5):
        assert login(api, email="nobody@acme.co.uk", password="guess").status_code == 401
    assert login(api, email="nobody@acme.co.uk", password="guess").status_code == 429


def test_blocked_attempts_are_not_recorded(api, db, user):
    for _ in range(7):
        login(api, password="not the password")
    # 5 real failures recorded; the 2 blocked ones don't extend the lockout.
    assert db.scalar(select(func.count()).select_from(LoginAttempt)) == 5


# --- using the access token -----------------------------------------------------


def test_me_requires_a_token(api):
    res = api.get("/api/v1/me")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "not_authenticated"


def test_me_returns_the_logged_in_user(api, user):
    token = login(api).json()["access_token"]
    res = api.get("/api/v1/me", headers=bearer(token))
    assert res.status_code == 200
    assert res.json()["email"] == EMAIL


def test_expired_access_token_rejected_with_specific_code(api, db, user):
    login(api)
    session_id = db.scalars(select(UserSession.id)).one()
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": str(user.id), "sid": str(session_id), "typ": "access", "iat": past, "exp": past},
        get_settings().jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    res = api.get("/api/v1/me", headers=bearer(expired))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "token_expired"  # frontend knows to call /refresh


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        jwt.encode({"sub": "x"}, "an-attacker-guessed-secret-of-32-plus-chars", algorithm="HS256"),
        # "alg: none" tokens must never be accepted.
        jwt.encode(
            {"sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "typ": "access"},
            key=None,
            algorithm="none",
        ),
    ],
)
def test_forged_or_malformed_tokens_rejected(api, token):
    res = api.get("/api/v1/me", headers=bearer(token))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_token"


def test_token_for_disabled_user_stops_working(api, db, user):
    token = login(api).json()["access_token"]
    db.execute(update(User).values(is_active=False))
    res = api.get("/api/v1/me", headers=bearer(token))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "session_ended"


def test_limited_access_until_email_verified(app, api, db, user):
    probe = APIRouter()

    @probe.get("/api/v1/_test/verified-only")
    def verified_only(u: VerifiedUser):
        return {"ok": True}

    app.include_router(probe)
    token = login(api).json()["access_token"]

    blocked = api.get("/api/v1/_test/verified-only", headers=bearer(token))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "email_not_verified"

    db.execute(update(User).values(email_verified_at=func.now()))
    assert api.get("/api/v1/_test/verified-only", headers=bearer(token)).status_code == 200


# --- refresh ------------------------------------------------------------------


def test_refresh_issues_new_tokens_and_rotates_the_cookie(api, user):
    login(api)
    old_cookie = api.cookies.get(COOKIE)

    res = api.post("/api/v1/auth/refresh")
    assert res.status_code == 200
    new_cookie = api.cookies.get(COOKIE)
    assert new_cookie and new_cookie != old_cookie
    assert api.get("/api/v1/me", headers=bearer(res.json()["access_token"])).status_code == 200


def test_old_refresh_token_stops_working_after_rotation(app, api, user):
    from fastapi.testclient import TestClient

    login(api)
    old_cookie = api.cookies.get(COOKIE)
    assert api.post("/api/v1/auth/refresh").status_code == 200

    # Someone replaying the stolen pre-rotation token from another client:
    replay = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    res = replay.post("/api/v1/auth/refresh", headers={"Cookie": f"{COOKIE}={old_cookie}"})
    assert res.status_code == 401
    assert 'vyterlix_refresh=""' in res.headers["set-cookie"]  # told to drop the dead cookie


def test_refresh_without_cookie_fails(api):
    res = api.post("/api/v1/auth/refresh")
    assert res.status_code == 401


def test_refresh_fails_for_expired_session(api, db, user):
    login(api)
    db.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert api.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_fails_and_revokes_for_disabled_user(api, db, user):
    login(api)
    db.execute(update(User).values(is_active=False))
    assert api.post("/api/v1/auth/refresh").status_code == 401
    assert db.scalars(select(UserSession)).one().revoked_at is not None


# --- logout -------------------------------------------------------------------


def test_logout_revokes_session_and_access_token_immediately(api, db, user):
    token = login(api).json()["access_token"]
    res = api.post("/api/v1/auth/logout")
    assert res.status_code == 204
    assert 'vyterlix_refresh=""' in res.headers["set-cookie"]

    assert db.scalars(select(UserSession)).one().revoked_at is not None
    me = api.get("/api/v1/me", headers=bearer(token))  # token not expired, but session is gone
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "session_ended"
    assert "auth.logout" in db.scalars(select(AuditLog.action)).all()


def test_logout_without_session_is_harmless(api):
    assert api.post("/api/v1/auth/logout").status_code == 204


def test_logging_out_one_device_keeps_the_other(app, api, db, user):
    from fastapi.testclient import TestClient

    phone = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    laptop_token = login(api).json()["access_token"]
    phone_token = login(phone).json()["access_token"]

    api.post("/api/v1/auth/logout")
    assert api.get("/api/v1/me", headers=bearer(laptop_token)).status_code == 401
    assert phone.get("/api/v1/me", headers=bearer(phone_token)).status_code == 200


# --- configuration safety -------------------------------------------------------


def test_prod_refuses_the_dev_jwt_secret():
    with pytest.raises(ValidationError, match="VYTERLIX_JWT_SECRET"):
        Settings(env="prod", _env_file=None)
    with pytest.raises(ValidationError, match="VYTERLIX_JWT_SECRET"):
        Settings(env="staging", jwt_secret="too-short", _env_file=None)
    assert Settings(env="dev", _env_file=None).jwt_secret == DEV_JWT_SECRET


def test_access_token_lifetime_matches_settings():
    _, lifetime = create_access_token(uuid.uuid4(), uuid.uuid4())
    assert lifetime == get_settings().access_token_ttl_minutes * 60
