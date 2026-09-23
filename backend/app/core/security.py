"""Password hashing (Argon2id), single-use tokens and access-token JWTs."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# Hash of a random throwaway password, verified when the email is unknown so a failed
# login takes the same time whether or not the account exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_password_check_time(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def password_needs_rehash(password_hash: str) -> bool:
    """True when hashing parameters have been strengthened since this hash was made."""
    return _hasher.check_needs_rehash(password_hash)


def hash_token(raw_token: str) -> str:
    """SHA-256 hex digest. Tokens are high-entropy random values, so a fast hash is enough."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """Return (raw, hashed). Send the raw token to the user; store only the hash."""
    raw = secrets.token_urlsafe(32)  # 256 bits
    return raw, hash_token(raw)


class AccessTokenError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code  # "token_expired" or "invalid_token"


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
    """Return (jwt, lifetime_seconds)."""
    settings = get_settings()
    lifetime = settings.access_token_ttl_minutes * 60
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM), lifetime


def decode_access_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (user_id, session_id) or raise AccessTokenError."""
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],  # pinned: never accept "none" or other algorithms
            options={"require": ["sub", "sid", "exp", "iat", "typ"]},
        )
        if claims["typ"] != "access":
            raise AccessTokenError("invalid_token")
        return uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenError("token_expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AccessTokenError("invalid_token") from exc
