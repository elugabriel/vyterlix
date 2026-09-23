"""Password hashing (Argon2id) and single-use token generation."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


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
