"""Encrypting the keys that let Vyterlix read a connected system (Xero, Shopify...).

Fernet: AES-128-CBC with an HMAC, so a tampered value fails to decrypt instead of returning
garbage. The key lives in the environment (VYTERLIX_ENCRYPTION_KEY), never in the database,
so a copy of the database alone does not reveal anyone's tokens.

Rotation: put the new key in `encryption_key` and keep the old one(s) in
`previous_encryption_keys`. Old values still decrypt; `reencrypt` moves one to the new key.
"""

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class CryptoError(Exception):
    """A stored value could not be decrypted (wrong or missing key, or it was altered)."""


@lru_cache
def _fernet_for(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in keys])


def _fernet() -> MultiFernet:
    settings = get_settings()
    return _fernet_for((settings.encryption_key, *settings.previous_encryption_keys))


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("Stored value can't be decrypted") from exc


def encrypt_json(value: dict[str, Any]) -> str:
    return encrypt_text(json.dumps(value, separators=(",", ":"), sort_keys=True))


def decrypt_json(token: str) -> dict[str, Any]:
    return json.loads(decrypt_text(token))


def reencrypt(token: str) -> str:
    """The same value encrypted with the newest key (for key rotation)."""
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("Stored value can't be decrypted") from exc
