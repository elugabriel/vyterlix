"""Forgot password → emailed link → reset password.

Deliberately never reveals whether an email is registered, and a successful reset
logs the user out everywhere, because a reset often means someone else had the password.
"""

from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_password
from app.models.identity import LoginAttempt, User, UserSession
from app.schemas.auth import password_matches_email
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.email import EmailMessage, EmailSender
from app.services.tokens import consume_token, issue_token, seconds_since_last_token

PASSWORD_RESET = "password_reset"


def _invalid_token() -> AppError:
    return AppError("This reset link is invalid or has expired", code="invalid_token")


def request_password_reset(db: Session, email: str, sender: EmailSender, meta: RequestMeta) -> None:
    """Email a reset link if the account exists and is active. Always returns quietly."""
    settings = get_settings()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return

    age = seconds_since_last_token(db, user, PASSWORD_RESET)
    if age is not None and age < settings.token_resend_cooldown_seconds:
        return  # stops the endpoint being used to flood an inbox

    raw = issue_token(
        db,
        user,
        PASSWORD_RESET,
        timedelta(minutes=settings.password_reset_ttl_minutes),
        meta.ip_address,
    )
    record_audit(
        db,
        AuditAction.AUTH_PASSWORD_RESET_REQUESTED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()

    link = f"{settings.frontend_base_url}/reset-password.html?{urlencode({'token': raw})}"
    sender.send(
        EmailMessage(
            to=user.email,
            subject="Reset your Vyterlix password",
            body=(
                f"Hi {user.full_name},\n\n"
                f"Someone asked to reset the password for this account. "
                f"To choose a new password, open this link:\n{link}\n\n"
                f"The link works once and expires in {settings.password_reset_ttl_minutes} "
                "minutes. If you didn't ask for this, you can ignore this email; "
                "your password won't change."
            ),
        )
    )


def reset_password(
    db: Session, raw_token: str, new_password: str, sender: EmailSender, meta: RequestMeta
) -> None:
    token = consume_token(db, raw_token, PASSWORD_RESET)
    user = db.get(User, token.user_id) if token else None
    if user is None or not user.is_active:
        db.rollback()
        raise _invalid_token()

    if password_matches_email(new_password, user.email):
        db.rollback()  # puts the token back unused, so the user can try another password
        raise AppError(
            "Password must not be the same as your email address",
            code="password_same_as_email",
            status_code=422,
        )

    user.password_hash = hash_password(new_password)
    user.password_changed_at = func.now()
    if user.email_verified_at is None:
        user.email_verified_at = func.now()  # opening the emailed link proves they own it

    # Log out every device: whoever had the old password must lose access now.
    revoked = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    ).rowcount
    # Lift the failed-login lockout so the owner can log straight in.
    db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.email == user.email, LoginAttempt.succeeded.is_(False)
        )
    )
    record_audit(
        db,
        AuditAction.AUTH_PASSWORD_RESET,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"sessions_revoked": revoked},
    )
    db.commit()
    send_password_changed_alert(sender, user, logged_out="on all devices")


def send_password_changed_alert(sender: EmailSender, user: User, *, logged_out: str) -> None:
    """Security alert after any password change, so the owner hears about one they didn't make."""
    sender.send(
        EmailMessage(
            to=user.email,
            subject="Your Vyterlix password was changed",
            body=(
                f"Hi {user.full_name},\n\n"
                "The password for your Vyterlix account was just changed, and you've been "
                f"logged out {logged_out}.\n\n"
                "If this was you, there's nothing else to do. If it wasn't, reset your "
                f"password now at {get_settings().frontend_base_url}/forgot-password.html "
                "and contact support."
            ),
        )
    )
