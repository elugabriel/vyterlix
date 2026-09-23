"""The logged-in user managing their own account."""

import uuid

from sqlalchemy import delete, func, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.identity import LoginAttempt, User, UserSession, UserToken
from app.schemas.auth import password_matches_email
from app.services.audit import record_audit
from app.services.auth import RequestMeta
from app.services.email import EmailSender
from app.services.password_reset import PASSWORD_RESET, send_password_changed_alert
from app.services.sessions import check_login_rate_limits


def update_profile(db: Session, user: User, changes: dict, meta: RequestMeta) -> User:
    """`changes` holds only the fields the caller actually sent (already validated)."""
    changed = sorted(field for field, value in changes.items() if getattr(user, field) != value)
    for field in changed:
        setattr(user, field, changes[field])
    if changed:
        record_audit(
            db,
            "user.profile_updated",
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"fields": changed},  # names only, never values
        )
        db.commit()
        db.refresh(user)
    return user


def change_password(
    db: Session,
    user: User,
    current_session_id: uuid.UUID,
    current_password: str,
    new_password: str,
    sender: EmailSender,
    meta: RequestMeta,
) -> None:
    # Wrong current passwords count toward the login lockout, so a stolen access token
    # can't be used to guess the password here instead of on /auth/login.
    check_login_rate_limits(db, user.email, meta.ip_address)
    if not verify_password(user.password_hash, current_password):
        db.add(LoginAttempt(email=user.email, ip_address=meta.ip_address, succeeded=False))
        record_audit(
            db,
            "user.password_change_failed",
            actor_user_id=user.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )
        db.commit()
        raise AppError(
            "Your current password is incorrect", code="incorrect_password", status_code=400
        )

    if password_matches_email(new_password, user.email):
        raise AppError(
            "Password must not be the same as your email address",
            code="password_same_as_email",
            status_code=422,
        )
    if verify_password(user.password_hash, new_password):
        raise AppError(
            "Choose a password different from your current one",
            code="password_unchanged",
            status_code=422,
        )

    user.password_hash = hash_password(new_password)
    user.password_changed_at = func.now()
    # Keep this device logged in; log out every other one.
    revoked = db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.id != current_session_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    ).rowcount
    # A reset link requested before the change must not be able to undo it.
    db.execute(
        delete(UserToken).where(
            UserToken.user_id == user.id,
            UserToken.purpose == PASSWORD_RESET,
            UserToken.used_at.is_(None),
        )
    )
    record_audit(
        db,
        "user.password_changed",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"other_sessions_revoked": revoked},
    )
    db.commit()
    send_password_changed_alert(sender, user, logged_out="on your other devices")
