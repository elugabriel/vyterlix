from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.security import hash_password
from app.models.identity import User
from app.services.audit import record_audit


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
