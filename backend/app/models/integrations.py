"""Connections to other systems (Phase 4 Part B): Xero, Shopify, WooCommerce, Google Analytics.

- integrations: one connection from a business to one account at a provider (one Xero
  organisation, one Shopify shop). Holds who connected it, what we may read, how the last sync
  went, and the access/refresh tokens, encrypted (services/crypto.py). Disconnecting wipes the
  tokens but keeps the row, so history and "where did this record come from" still work.
- integration_syncs: one run of "fetch the latest from the provider": counts, outcome, error,
  the background job that ran it, and the import it produced (provenance).
- integration_oauth_states: a "connect" attempt in progress. The random `state` value (stored
  only as a hash) proves the person returning from the provider is the one who started it;
  the PKCE verifier is kept encrypted. Single use, short lived.

`provider` is a free-form code checked by format only, so adding a provider needs no migration.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

INTEGRATION_STATUSES = (
    "connected",  # working
    "needs_reauth",  # the provider no longer accepts our tokens: sign in again
    "disconnected",  # the person disconnected it; tokens wiped
)
SYNC_STATUSES = ("running", "succeeded", "failed")
SYNC_TRIGGERS = ("manual", "scheduled")


def _tenant_fk(column: str, target: str, *, ondelete: str | None = None) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete=ondelete,
    )


class Integration(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_integrations_org_id"),
        # Reconnecting the same account revives this row instead of adding a second one.
        UniqueConstraint(
            "organization_id", "provider", "external_account_id", name="uq_integrations_account"
        ),
        CheckConstraint("provider ~ '^[a-z][a-z_]*$'", name="provider_format"),
        CheckConstraint(_one_of("status", INTEGRATION_STATUSES), name="status_valid"),
        CheckConstraint("length(trim(external_account_id)) > 0", name="account_not_blank"),
        CheckConstraint(
            "status = 'disconnected' OR credentials IS NOT NULL", name="connected_has_credentials"
        ),
        CheckConstraint(
            "status <> 'disconnected' OR (credentials IS NULL AND disconnected_at IS NOT NULL)",
            name="disconnected_has_no_credentials",
        ),
        CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN ('succeeded', 'failed')",
            name="last_sync_status_valid",
        ),
        CheckConstraint("consecutive_failures >= 0", name="failures_not_negative"),
        Index("ix_integrations_org_provider", "organization_id", "provider"),
    )

    provider: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(15), server_default="connected")
    display_name: Mapped[str] = mapped_column(String(150))  # "Xero · Acme Bakery Ltd"
    external_account_id: Mapped[str] = mapped_column(String(200))  # the provider's id for it
    external_account_name: Mapped[str | None] = mapped_column(String(200))
    granted_scopes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    # Provider-specific, not secret: e.g. {"shop_domain": "acme.myshopify.com"}.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))

    # Fernet token of {"access_token", "refresh_token", "expires_at", "scopes", "extra"}.
    # Never selected into an API response (schemas/integrations.py has no such field).
    credentials: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(10))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Where the last sync got to, so the next one fetches only what is new.
    sync_cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class IntegrationSync(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "integration_syncs"
    __table_args__ = (
        _tenant_fk("integration_id", "integrations", ondelete="CASCADE"),
        _tenant_fk("data_import_id", "data_imports"),
        CheckConstraint(_one_of("status", SYNC_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("trigger", SYNC_TRIGGERS), name="trigger_valid"),
        CheckConstraint(
            "records_fetched >= 0 AND records_created >= 0", name="counts_not_negative"
        ),
        CheckConstraint("status = 'running' OR finished_at IS NOT NULL", name="finished_has_time"),
        Index("ix_integration_syncs_integration", "integration_id", "started_at"),
    )

    integration_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(10), server_default="running")
    trigger: Mapped[str] = mapped_column(String(10), server_default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_fetched: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    records_created: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))
    job_id: Mapped[uuid.UUID | None] = mapped_column()  # the background job that ran it
    # What this sync brought in, so every record can be traced to where it came from.
    data_import_id: Mapped[uuid.UUID | None] = mapped_column()
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class IntegrationOAuthState(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "integration_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_integration_oauth_states_hash"),
        CheckConstraint("provider ~ '^[a-z][a-z_]*$'", name="provider_format"),
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="state_hash_format"),
        Index("ix_integration_oauth_states_expires", "expires_at"),
    )

    provider: Mapped[str] = mapped_column(String(30))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Set when re-connecting an existing connection: the account must turn out to be the same.
    integration_id: Mapped[uuid.UUID | None] = mapped_column()
    state_hash: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(Text)  # encrypted
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
