"""What every connector must do (Xero, Shopify, WooCommerce, Google Analytics, ...).

A connector is one small class. The framework around it (services/integrations.py) does the
rest: the OAuth hand-shake, encrypting and refreshing tokens, running syncs in the background,
recording how each sync went, telling the person when they need to sign in again.

A provider only talks to its own system and never touches the database. It raises one of the
errors below so the framework knows what to do:

- ReauthRequired:     the provider no longer accepts our tokens. Ask the person to sign in again.
- ProviderUnavailable: temporary (network, 5xx, rate limit). The sync is retried later.
- ProviderRejected:   permanent for this request (bad request, missing scope). No retry.

Every message is shown to the person, so write it in plain English, never a raw API error.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str = "provider_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class ReauthRequired(ProviderError):
    def __init__(self, message: str = "Please sign in again to keep this connected."):
        super().__init__(message, code="reauth_required")


class ProviderUnavailable(ProviderError):
    def __init__(self, message: str = "The other system isn't responding. We'll try again."):
        super().__init__(message, code="provider_unavailable")


class ProviderRejected(ProviderError):
    def __init__(self, message: str, *, code: str = "provider_rejected"):
        super().__init__(message, code=code)


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None  # None = does not expire
    scopes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)  # provider-specific, stored encrypted too


@dataclass(frozen=True)
class AccountInfo:
    external_id: str  # the provider's stable id for this account (not its name)
    name: str
    settings: dict[str, Any] = field(default_factory=dict)  # not secret, e.g. shop domain


@dataclass
class SyncContext:
    """What a sync gets. `tokens` are always valid (refreshed first if about to expire)."""

    tokens: TokenSet
    account: AccountInfo
    cursor: dict[str, Any]  # what the previous sync saved; empty on the first sync
    settings: dict[str, Any]
    progress: Callable[[int, int], None]
    now: datetime


@dataclass(frozen=True)
class SyncOutcome:
    records_fetched: int = 0
    records_created: int = 0
    cursor: dict[str, Any] | None = None  # saved for the next sync (None = keep the old one)
    data_import_id: Any = None  # the import this sync produced, for provenance


class Provider(ABC):
    key: str  # lowercase code stored in the database, e.g. "xero"
    label: str  # shown to people, e.g. "Xero"
    scopes: tuple[str, ...] = ()
    # What we read, in plain English. Shown BEFORE connecting. Read-only providers only.
    permissions: tuple[str, ...] = ()

    def is_configured(self, settings: Settings) -> bool:
        """False until the business running Vyterlix has registered with this provider."""
        return True

    @abstractmethod
    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        """Where to send the person to approve access."""

    @abstractmethod
    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        """Swap the one-time code for tokens."""

    @abstractmethod
    def refresh(self, tokens: TokenSet) -> TokenSet:
        """New tokens from the refresh token. Raise ReauthRequired if it no longer works."""

    @abstractmethod
    def account(self, tokens: TokenSet) -> AccountInfo:
        """Which account the tokens belong to."""

    def revoke(self, tokens: TokenSet) -> None:  # noqa: B027  (optional: most can skip it)
        """Tell the provider to forget our access (best effort; failures are ignored)."""

    @abstractmethod
    def sync(self, ctx: SyncContext) -> SyncOutcome:
        """Fetch what is new and bring it into the business's data."""
