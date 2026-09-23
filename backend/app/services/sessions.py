"""Login, refresh and logout.

A login creates a UserSession (one per device). The browser holds:
  - a short-lived access JWT (in page memory), carrying the session id, and
  - a long-lived refresh token (httpOnly cookie), stored here only as a SHA-256 hash.
Refreshing rotates the refresh token. Logging out, or revoking the session, cuts off
the access token immediately too, because every request checks its session is live.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, AuthenticationError, TooManyRequestsError
from app.core.security import (
    burn_password_check_time,
    create_access_token,
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models.identity import LoginAttempt, User, UserSession
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    session_id: uuid.UUID
    access_token: str
    expires_in: int
    refresh_token: str  # raw value; goes into the cookie only


def _invalid_credentials() -> AuthenticationError:
    # One message for wrong password and unknown email: nothing to probe.
    return AuthenticationError("Incorrect email or password", code="invalid_credentials")


def check_login_rate_limits(db: Session, email: str, meta: RequestMeta) -> None:
    settings = get_settings()
    since = func.now() - timedelta(minutes=settings.login_failure_window_minutes)
    failures = select(func.count()).where(
        LoginAttempt.succeeded.is_(False), LoginAttempt.created_at > since
    )
    reason = None
    if db.scalar(failures.where(LoginAttempt.email == email)) >= (
        settings.login_max_failures_per_email
    ):
        reason = "email_limit"
    elif (
        meta.ip_address is not None
        and db.scalar(failures.where(LoginAttempt.ip_address == meta.ip_address))
        >= settings.login_max_failures_per_ip
    ):
        reason = "ip_limit"
    if reason:
        # The clearest sign of password guessing, so it's always audited.
        record_audit(
            db,
            AuditAction.AUTH_LOGIN_BLOCKED,
            actor_user_id=db.scalar(select(User.id).where(User.email == email)),
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"reason": reason},
        )
        db.commit()
        raise TooManyRequestsError(
            "Too many failed login attempts. Please wait a few minutes, "
            "or reset your password if you've forgotten it.",
            code="too_many_login_attempts",
        )


def _start_session(db: Session, user: User, meta: RequestMeta) -> IssuedTokens:
    raw_refresh, refresh_hash = new_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        user_agent=meta.user_agent[:500] if meta.user_agent else None,
        ip_address=meta.ip_address,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_ttl_days),
    )
    db.add(session)
    db.flush()
    access, lifetime = create_access_token(user.id, session.id)
    return IssuedTokens(user, session.id, access, lifetime, raw_refresh)


def login(db: Session, email: str, password: str, meta: RequestMeta) -> IssuedTokens:
    """`email` must already be normalised. Unverified users may log in (limited access)."""
    check_login_rate_limits(db, email, meta)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check_time(password)
        ok = False
    else:
        ok = verify_password(user.password_hash, password)

    db.add(LoginAttempt(email=email, ip_address=meta.ip_address, succeeded=ok))
    if not ok:
        record_audit(
            db,
            AuditAction.AUTH_LOGIN_FAILED,
            actor_user_id=user.id if user else None,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details=None if user else {"reason": "unknown_email"},
        )
        db.commit()  # keep the failed attempt even though the request errors
        raise _invalid_credentials()

    if not user.is_active:
        record_audit(
            db,
            AuditAction.AUTH_LOGIN_FAILED,
            actor_user_id=user.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"reason": "account_disabled"},
        )
        db.commit()
        raise AppError("This account has been disabled", code="account_disabled", status_code=403)

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = func.now()
    tokens = _start_session(db, user, meta)
    record_audit(
        db,
        AuditAction.AUTH_LOGIN_SUCCEEDED,
        actor_user_id=user.id,
        target_type="session",
        target_id=tokens.session_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()
    db.refresh(user)
    return tokens


def _live_session_by_refresh(db: Session, raw_refresh: str) -> UserSession | None:
    return db.scalar(
        select(UserSession)
        .where(
            UserSession.refresh_token_hash == hash_token(raw_refresh),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > func.now(),
        )
        .with_for_update()
    )


def refresh(db: Session, raw_refresh: str | None) -> IssuedTokens:
    """Swap a valid refresh token for a new access token and a new refresh token."""
    session = _live_session_by_refresh(db, raw_refresh) if raw_refresh else None
    if session is None:
        raise AuthenticationError("Your session has ended. Please log in again.")

    user = db.get(User, session.user_id)
    if not user.is_active:
        session.revoked_at = func.now()
        db.commit()
        raise AuthenticationError("Your session has ended. Please log in again.")

    raw_new, new_hash = new_token()
    session.refresh_token_hash = new_hash  # old refresh token stops working now
    session.last_used_at = func.now()
    access, lifetime = create_access_token(user.id, session.id)
    db.commit()
    return IssuedTokens(user, session.id, access, lifetime, raw_new)


def logout(db: Session, raw_refresh: str | None, meta: RequestMeta) -> None:
    """End the session behind this refresh cookie. Quietly does nothing if there isn't one."""
    session = _live_session_by_refresh(db, raw_refresh) if raw_refresh else None
    if session is None:
        return
    session.revoked_at = func.now()
    record_audit(
        db,
        AuditAction.AUTH_LOGOUT,
        actor_user_id=session.user_id,
        target_type="session",
        target_id=session.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()


def load_session_user(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> User | None:
    """The user behind an access token, or None if the session was revoked/expired or the
    user is disabled."""
    return db.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            UserSession.id == session_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > func.now(),
        )
    )
