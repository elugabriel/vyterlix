"""Connecting a business to another system, keeping the connection healthy, and syncing it.

The flow (OAuth 2.0 with PKCE):
  1. start_connect   Owner presses "Connect". We store a one-time `state` (hashed) and a PKCE
                     verifier (encrypted) and return the provider's approval page address.
  2. The person approves at the provider, which sends them back to our callback page with a
     `code` and the `state`.
  3. complete_connect  The page posts both here. We check the state belongs to this person and
                     business, hasn't expired or been used, swap the code for tokens, ask the
                     provider whose account it is, and save the tokens encrypted.
  4. run_sync        A background job (services/jobs.py) fetches what is new, using tokens that
                     are refreshed first if they are about to expire.

Failure handling is the point of the framework: a provider that no longer accepts our tokens puts
the connection in "needs_reauth" and tells the person; a temporary outage is retried by the job
queue; every sync, good or bad, leaves a row in integration_syncs.

The session must be scoped to the organisation (CurrentTenant, or a job's tenant_scope).
"""

import base64
import hashlib
import logging
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.integrations.base import (
    AccountInfo,
    ProviderError,
    ProviderUnavailable,
    ReauthRequired,
    SyncContext,
    TokenSet,
    utcnow,
)
from app.integrations.registry import PROVIDERS, get_provider
from app.models.integrations import Integration, IntegrationOAuthState, IntegrationSync
from app.schemas.integrations import ConnectOut, IntegrationOut, SyncOut
from app.services import billing, system_events
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.crypto import CryptoError, decrypt_json, decrypt_text, encrypt_json, encrypt_text
from app.services.jobs import JobContext, enqueue

logger = logging.getLogger("vyterlix.integrations")

FAILING_AFTER = 3  # failures in a row before staff are told
GENERIC_SYNC_FAILURE = "Something went wrong while fetching your data. We'll try again."


class TemporaryProblem(Exception):
    """Raised out of a sync for a temporary problem, so the job queue retries it later."""


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _redirect_uri() -> str:
    settings = get_settings()
    return settings.frontend_base_url.rstrip("/") + settings.integration_callback_path


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def get_integration(db: Session, integration_id: uuid.UUID) -> Integration:
    integration = db.get(Integration, integration_id)  # tenant scope hides other businesses'
    if integration is None:
        raise NotFoundError("Connection not found")
    return integration


def _label(provider_key: str) -> str:
    provider = PROVIDERS.get(provider_key)
    return provider.label if provider else provider_key.replace("_", " ").title()


# --- presenting a connection (never any credentials) -------------------------------------------


def integration_out(i: Integration) -> IntegrationOut:
    provider = PROVIDERS.get(i.provider)
    return IntegrationOut(
        id=i.id,
        provider=i.provider,
        provider_label=_label(i.provider),
        display_name=i.display_name,
        status=i.status,
        account_name=i.external_account_name,
        permissions=list(provider.permissions) if provider else [],
        connected_at=i.connected_at,
        disconnected_at=i.disconnected_at,
        last_sync_at=i.last_sync_at,
        last_successful_sync_at=i.last_successful_sync_at,
        last_sync_status=i.last_sync_status,
        last_error_code=i.last_error_code,
        last_error_message=i.last_error_message,
        last_error_at=i.last_error_at,
        consecutive_failures=i.consecutive_failures,
        needs_attention=i.status == "needs_reauth"
        or (i.status == "connected" and i.consecutive_failures > 0),
    )


def list_integrations(db: Session) -> list[IntegrationOut]:
    rows = db.scalars(select(Integration).order_by(Integration.connected_at.desc(), Integration.id))
    return [integration_out(i) for i in rows]


def list_syncs(db: Session, integration_id: uuid.UUID, limit: int = 20) -> list[SyncOut]:
    get_integration(db, integration_id)
    rows = db.scalars(
        select(IntegrationSync)
        .where(IntegrationSync.integration_id == integration_id)
        .order_by(IntegrationSync.started_at.desc(), IntegrationSync.id)
        .limit(limit)
    )
    return [SyncOut.model_validate(r) for r in rows]


# --- connecting ------------------------------------------------------------------------------


def start_connect(
    db: Session,
    tenant,
    provider_key: str,
    integration_id: uuid.UUID | None = None,
    *,
    now: datetime | None = None,
) -> ConnectOut:
    now = now or utcnow()
    provider = get_provider(provider_key)
    if integration_id is None:  # a new connection: the plan has room for one more
        billing.check(db, tenant.organization_id, "integrations")
    if integration_id is not None:
        existing = get_integration(db, integration_id)
        if existing.provider != provider.key:
            raise AppError(
                "That isn't a connection to this provider.", code="wrong_provider", status_code=422
            )
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce()
    ttl = timedelta(minutes=get_settings().oauth_state_ttl_minutes)
    db.add(
        IntegrationOAuthState(
            provider=provider.key,
            user_id=tenant.user.id,
            integration_id=integration_id,
            state_hash=_hash(state),
            code_verifier=encrypt_text(verifier),
            expires_at=now + ttl,
        )
    )
    # Housekeeping: attempts nobody finished.
    db.execute(
        delete(IntegrationOAuthState).where(
            IntegrationOAuthState.expires_at < now - timedelta(days=1)
        )
    )
    db.commit()
    url = provider.authorize_url(
        state=state, redirect_uri=_redirect_uri(), code_challenge=challenge
    )
    return ConnectOut(authorize_url=url, expires_in_minutes=int(ttl.total_seconds() // 60))


def _token_payload(tokens: TokenSet) -> dict[str, Any]:
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
        "scopes": list(tokens.scopes),
        "extra": tokens.extra,
    }


def _tokens_of(integration: Integration) -> TokenSet:
    data = decrypt_json(integration.credentials)
    expires = data.get("expires_at")
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.fromisoformat(expires) if expires else None,
        scopes=tuple(data.get("scopes", ())),
        extra=data.get("extra", {}),
    )


def _store_tokens(integration: Integration, tokens: TokenSet) -> None:
    integration.credentials = encrypt_json(_token_payload(tokens))
    integration.token_expires_at = tokens.expires_at
    if tokens.scopes:
        integration.granted_scopes = list(tokens.scopes)


def complete_connect(
    db: Session,
    tenant,
    state: str,
    code: str,
    meta: RequestMeta,
    *,
    now: datetime | None = None,
) -> IntegrationOut:
    now = now or utcnow()
    # Read these first: a commit below can expire the ORM objects behind `tenant`.
    user_id, organization_id = tenant.user.id, tenant.organization_id
    row = db.scalars(
        select(IntegrationOAuthState)
        .where(IntegrationOAuthState.state_hash == _hash(state))
        .with_for_update()
    ).first()
    if row is None or row.used_at is not None or row.expires_at <= now or row.user_id != user_id:
        raise AppError(
            "This sign-in has expired or was already used. Please start connecting again.",
            code="invalid_state",
        )
    provider_key, target_id = row.provider, row.integration_id
    verifier = decrypt_text(row.code_verifier)
    row.used_at = now  # single use, even if the steps below fail
    db.commit()

    provider = get_provider(provider_key)
    try:
        tokens = provider.exchange_code(
            code=code, redirect_uri=_redirect_uri(), code_verifier=verifier
        )
        account = provider.account(tokens)
    except ProviderUnavailable as exc:
        raise AppError(exc.message, code=exc.code, status_code=503) from exc
    except ProviderError as exc:
        raise AppError(exc.message, code=exc.code, status_code=422) from exc

    if target_id is not None:
        integration = get_integration(db, target_id)
        if integration.external_account_id != account.external_id:
            raise ConflictError(
                f"That is a different {provider.label} account from the one connected before. "
                "Sign in with the same account, or disconnect this one and connect the other.",
                code="different_account",
            )
    else:
        integration = db.scalars(
            select(Integration).where(
                Integration.provider == provider.key,
                Integration.external_account_id == account.external_id,
            )
        ).first()
    if integration is None:
        integration = Integration(
            provider=provider.key, external_account_id=account.external_id, connected_at=now
        )
        db.add(integration)
    integration.display_name = f"{provider.label} · {account.name}"[:150]
    integration.external_account_name = account.name
    integration.settings = account.settings
    integration.status = "connected"
    integration.connected_by_user_id = user_id
    integration.connected_at = now
    integration.disconnected_at = None
    integration.last_error_code = integration.last_error_message = integration.last_error_at = None
    integration.consecutive_failures = 0
    _store_tokens(integration, tokens)
    db.flush()
    record_audit(
        db,
        AuditAction.INTEGRATION_CONNECTED,
        actor_user_id=user_id,
        organization_id=organization_id,
        target_type="integration",
        target_id=integration.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details={"provider": provider.key, "account": account.name},
    )
    db.commit()
    return integration_out(integration)


# --- keeping the connection healthy ---------------------------------------------------------------


def valid_tokens(
    db: Session, integration: Integration, provider, *, now: datetime | None = None
) -> TokenSet:
    """The tokens, refreshed first if they are about to expire. Raises ReauthRequired if the
    provider no longer accepts them (or they can't be decrypted)."""
    now = now or utcnow()
    try:
        tokens = _tokens_of(integration)
        margin = timedelta(seconds=get_settings().token_refresh_margin_seconds)
        if tokens.expires_at is None or tokens.expires_at - margin > now:
            return tokens
        # Lock the row and look again: another worker may have refreshed while we waited.
        db.execute(select(Integration.id).where(Integration.id == integration.id).with_for_update())
        db.refresh(integration)
        tokens = _tokens_of(integration)
        if tokens.expires_at is None or tokens.expires_at - margin > now:
            return tokens
    except (CryptoError, KeyError, ValueError) as exc:
        raise ReauthRequired(
            "We can no longer read the saved sign-in for this connection. Please connect again."
        ) from exc
    fresh = provider.refresh(tokens)
    if fresh.refresh_token is None:  # many providers only send a new refresh token sometimes
        fresh = replace(fresh, refresh_token=tokens.refresh_token)
    _store_tokens(integration, fresh)
    db.commit()
    return fresh


def disconnect(
    db: Session,
    tenant,
    integration_id: uuid.UUID,
    meta: RequestMeta,
    *,
    now: datetime | None = None,
) -> IntegrationOut:
    """Forget the tokens (telling the provider if we can). Imported data and history stay."""
    now = now or utcnow()
    user_id, organization_id = tenant.user.id, tenant.organization_id
    integration = get_integration(db, integration_id)
    if integration.status != "disconnected":
        provider = PROVIDERS.get(integration.provider)
        if provider is not None and integration.credentials:
            try:
                provider.revoke(_tokens_of(integration))
            except Exception:  # best effort: the person still wants it gone
                logger.warning("Could not revoke %s tokens", integration.provider, exc_info=True)
        integration.credentials = None
        integration.token_expires_at = None
        integration.status = "disconnected"
        integration.disconnected_at = now
        record_audit(
            db,
            AuditAction.INTEGRATION_DISCONNECTED,
            actor_user_id=user_id,
            organization_id=organization_id,
            target_type="integration",
            target_id=integration.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"provider": integration.provider},
        )
        db.commit()
    return integration_out(integration)


# --- syncing -------------------------------------------------------------------------------------


def request_sync(db: Session, tenant, integration_id: uuid.UUID, meta: RequestMeta):
    """Queue a background sync (needs the worker). One at a time per connection."""
    integration = get_integration(db, integration_id)
    _require_usable(integration)
    return enqueue(
        db,
        tenant,
        kind="integration.sync",
        subject_type="integration",
        subject_id=integration.id,
        meta=meta,
    )


def _require_usable(integration: Integration) -> None:
    label = _label(integration.provider)
    if integration.status == "disconnected":
        raise ConflictError(
            f"This {label} connection has been disconnected. Connect it again first.",
            code="disconnected",
        )
    if integration.status == "needs_reauth":
        raise ConflictError(
            f"{label} needs you to sign in again before it can sync.", code="reauth_required"
        )


def run_sync(ctx: JobContext) -> dict[str, Any]:
    """The `integration.sync` job."""
    db = ctx.db
    integration = get_integration(db, ctx.job.subject_id)
    _require_usable(integration)
    provider = get_provider(integration.provider)
    now = utcnow()
    sync = IntegrationSync(
        integration_id=integration.id,
        trigger="manual",
        started_at=now,
        job_id=ctx.job.id,
        requested_by_user_id=ctx.tenant.user.id,
    )
    db.add(sync)
    integration.last_sync_at = now
    db.commit()  # visible straight away as "running"
    sync_id, integration_id = sync.id, integration.id

    try:
        tokens = valid_tokens(db, integration, provider, now=now)
        outcome = provider.sync(
            SyncContext(
                tokens=tokens,
                account=AccountInfo(
                    integration.external_account_id,
                    integration.external_account_name or "",
                    dict(integration.settings),
                ),
                cursor=dict(integration.sync_cursor),
                settings=dict(integration.settings),
                progress=ctx.progress,
                now=now,
            )
        )
    except ReauthRequired as exc:
        db.rollback()
        _record_failure(db, sync_id, integration_id, exc.code, exc.message, needs_reauth=True)
        raise AppError(exc.message, code=exc.code, status_code=409) from exc
    except ProviderUnavailable as exc:
        db.rollback()
        _record_failure(db, sync_id, integration_id, exc.code, exc.message)
        raise TemporaryProblem(exc.message) from exc
    except ProviderError as exc:
        db.rollback()
        _record_failure(db, sync_id, integration_id, exc.code, exc.message)
        raise AppError(exc.message, code=exc.code, status_code=422) from exc
    except Exception:
        db.rollback()
        _record_failure(db, sync_id, integration_id, "internal_error", GENERIC_SYNC_FAILURE)
        raise

    finished = utcnow()
    sync = db.get(IntegrationSync, sync_id)
    integration = get_integration(db, integration_id)
    sync.status, sync.finished_at = "succeeded", finished
    sync.records_fetched, sync.records_created = outcome.records_fetched, outcome.records_created
    sync.data_import_id = outcome.data_import_id
    integration.last_sync_status = "succeeded"
    integration.last_successful_sync_at = finished
    integration.last_error_code = integration.last_error_message = integration.last_error_at = None
    integration.consecutive_failures = 0
    if outcome.cursor is not None:
        integration.sync_cursor = outcome.cursor
    db.commit()
    return {
        "sync_id": str(sync_id),
        "records_fetched": outcome.records_fetched,
        "records_created": outcome.records_created,
    }


def _record_failure(
    db: Session,
    sync_id: uuid.UUID,
    integration_id: uuid.UUID,
    code: str,
    message: str,
    *,
    needs_reauth: bool = False,
) -> None:
    now = utcnow()
    sync = db.get(IntegrationSync, sync_id)
    integration = get_integration(db, integration_id)
    sync.status, sync.finished_at = "failed", now
    sync.error_code, sync.error_message = code, message[:500]
    integration.last_sync_status = "failed"
    integration.last_error_code, integration.last_error_message = code, message[:500]
    integration.last_error_at = now
    integration.consecutive_failures += 1
    if integration.consecutive_failures == FAILING_AFTER and not needs_reauth:
        system_events.record(
            db,
            "integration_failing",
            "A connection keeps failing",
            organization_id=integration.organization_id,
            details={"provider": integration.provider, "error_code": code},
        )
    if needs_reauth:
        system_events.record(
            db,
            "integration_needs_signing_in",
            "A connection needs signing in again",
            severity="warning",
            organization_id=integration.organization_id,
            details={"provider": integration.provider, "error_code": code},
        )
        integration.status = "needs_reauth"
        record_audit(
            db,
            AuditAction.INTEGRATION_REAUTH_NEEDED,
            organization_id=integration.organization_id,
            target_type="integration",
            target_id=integration.id,
            details={"provider": integration.provider},
        )
    db.commit()
