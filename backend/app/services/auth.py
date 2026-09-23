from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.core.security import hash_password
from app.models.identity import User
from app.services.audit import record_audit
from app.services.email import EmailMessage, EmailSender
from app.services.tokens import consume_token, issue_token, seconds_since_last_token

EMAIL_VERIFICATION = "email_verification"


@dataclass(frozen=True)
class RequestMeta:
    """Who is calling, for audit records."""

    ip_address: str | None = None
    user_agent: str | None = None


def _email_taken() -> ConflictError:
    return ConflictError("An account with this email already exists", code="email_taken")


def register_user(
    db: Session, *, email: str, password: str, full_name: str, meta: RequestMeta
) -> User:
    """Create an unverified user. `email` must already be normalised (lowercase)."""
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise _email_taken()

    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        # Two simultaneous sign-ups with the same email: the unique constraint decides.
        db.rollback()
        raise _email_taken() from exc

    record_audit(
        db,
        "user.registered",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()
    db.refresh(user)
    return user


def _invalid_token() -> AppError:
    # Same answer for unknown, expired and already-used tokens: nothing to probe.
    return AppError("This link is invalid or has expired", code="invalid_token")


def send_verification_email(
    db: Session, user: User, sender: EmailSender, meta: RequestMeta
) -> None:
    """Issue a fresh verification token (cancelling older ones) and email the link."""
    settings = get_settings()
    raw = issue_token(
        db,
        user,
        EMAIL_VERIFICATION,
        timedelta(hours=settings.email_verification_ttl_hours),
        meta.ip_address,
    )
    db.commit()  # the token must exist before the link can be clicked

    link = f"{settings.frontend_base_url}/verify-email.html?{urlencode({'token': raw})}"
    sender.send(
        EmailMessage(
            to=user.email,
            subject="Confirm your email for Vyterlix",
            body=(
                f"Hi {user.full_name},\n\n"
                f"Please confirm your email address by opening this link:\n{link}\n\n"
                f"The link expires in {settings.email_verification_ttl_hours} hours. "
                "If you didn't create a Vyterlix account, you can ignore this email."
            ),
        )
    )


def verify_email(db: Session, raw_token: str, meta: RequestMeta) -> User:
    token = consume_token(db, raw_token, EMAIL_VERIFICATION)
    if token is None:
        raise _invalid_token()

    user = db.get(User, token.user_id)
    if user.email_verified_at is None:
        user.email_verified_at = func.now()
    record_audit(
        db,
        "user.email_verified",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
    )
    db.commit()
    db.refresh(user)
    return user


def resend_verification(db: Session, email: str, sender: EmailSender, meta: RequestMeta) -> None:
    """Send a new link if the account exists and still needs verifying.

    Always returns quietly so the endpoint never reveals whether an email is registered.
    """
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or user.email_verified_at is not None:
        return

    age = seconds_since_last_token(db, user, EMAIL_VERIFICATION)
    if age is not None and age < get_settings().token_resend_cooldown_seconds:
        return  # too soon; stops the endpoint being used to flood an inbox

    send_verification_email(db, user, sender, meta)
