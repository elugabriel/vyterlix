"""Single-use, expiring user tokens — email verification now, password reset (step 5) next."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.security import hash_token, new_token
from app.models.identity import User, UserToken


def issue_token(
    db: Session, user: User, purpose: str, ttl: timedelta, requested_ip: str | None
) -> str:
    """Create a token and return the raw value. Any earlier unused token for the same
    purpose is deleted, so only the most recent link ever works."""
    db.execute(
        delete(UserToken).where(
            UserToken.user_id == user.id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
    )
    raw, hashed = new_token()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hashed,
            expires_at=datetime.now(UTC) + ttl,
            requested_ip=requested_ip,
        )
    )
    return raw


def consume_token(db: Session, raw_token: str, purpose: str) -> UserToken | None:
    """Mark a valid token used and return it, or None if unknown, expired, used or wrong purpose.

    The row lock stops two simultaneous requests from both using the same token.
    """
    token = db.scalar(
        select(UserToken)
        .where(
            UserToken.token_hash == hash_token(raw_token),
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
            UserToken.expires_at > func.now(),
        )
        .with_for_update()
    )
    if token is not None:
        token.used_at = func.now()
    return token


def seconds_since_last_token(db: Session, user: User, purpose: str) -> float | None:
    """Age of the newest token for this purpose, used for the resend cooldown."""
    created = db.scalar(
        select(func.max(UserToken.created_at)).where(
            UserToken.user_id == user.id, UserToken.purpose == purpose
        )
    )
    if created is None:
        return None
    return (datetime.now(UTC) - created).total_seconds()
