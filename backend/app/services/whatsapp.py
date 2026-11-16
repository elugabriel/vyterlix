"""What happens when WhatsApp sends us a notice (a "webhook").

Nothing is believed without a genuine signature: WhatsApp signs every notice with the business's own
Meta app secret, and a notice that does not check out is refused before it is even read. Each notice
goes to an address that names one connection (one WhatsApp number of one business), so there is no
guessing which business it belongs to.

What is kept from a notice: when each message was sent, which way it went (in from the customer, out
from the business) and a one-way code for the customer. Never the words, never the phone number.

Messages from customers arrive as `messages`. Replies sent through the WhatsApp Business Platform
arrive as `statuses` (the "sent" one), and replies typed in the WhatsApp Business app arrive as
`message_echoes`. Each message has its own id, so a message told to us twice is stored once.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.integrations.whatsapp import verify_token
from app.models.data import CustomerMessage
from app.models.integrations import Integration
from app.services.auth import RequestMeta
from app.services.integrations import stored_tokens
from app.services.jobs import enqueue

logger = logging.getLogger("vyterlix.whatsapp")

MAX_MESSAGES_PER_NOTICE = 1000
FIGURES_EVERY = timedelta(minutes=5)  # how often new messages trigger working the figures out again


def _integration(db: Session, integration_id: uuid.UUID) -> Integration:
    row = db.scalars(
        select(Integration)
        .where(Integration.id == integration_id, Integration.provider == "whatsapp")
        .execution_options(**ACROSS_TENANTS)
    ).first()
    if row is None or row.status != "connected":
        raise NotFoundError("Not found", code="not_found")
    return row


def verify(db: Session, integration_id: uuid.UUID, mode: str, token: str, challenge: str) -> str:
    """WhatsApp asks once, when the owner saves the address, whether it is really ours."""
    row = _integration(db, integration_id)
    secret = stored_tokens(row).refresh_token or ""
    phone_id = str(row.settings.get("phone_number_id") or row.external_account_id)
    expected = verify_token(secret, phone_id)
    if mode != "subscribe" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AppError("That check word is not right.", code="bad_verify_token", status_code=403)
    return challenge


def _signature_ok(secret: str, header: str | None, body: bytes) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256=") :], expected)


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _code(organization_id: uuid.UUID, number: Any) -> str | None:
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if not digits:
        return None
    return hashlib.sha256(f"{organization_id}:{digits}".encode()).hexdigest()


def _messages(body: dict[str, Any], phone_id: str, organization_id: uuid.UUID) -> list[dict]:
    """Every message told about for this number: {source_ref, sent_at, direction, contact_ref}."""
    found: list[dict] = []

    def add(ref: Any, sent: Any, direction: str, number: Any) -> None:
        sent_at, code = _when(sent), _code(organization_id, number)
        if ref and sent_at and code:
            found.append(
                {
                    "source_ref": str(ref)[:200],
                    "sent_at": sent_at,
                    "direction": direction,
                    "contact_ref": code,
                }
            )

    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if str((value.get("metadata") or {}).get("phone_number_id")) != phone_id:
                continue
            for m in value.get("messages") or []:
                add(m.get("id"), m.get("timestamp"), "in", m.get("from"))
            for s in value.get("statuses") or []:
                if s.get("status") == "sent":  # delivered and read are the same message again
                    add(s.get("id"), s.get("timestamp"), "out", s.get("recipient_id"))
            for e in value.get("message_echoes") or []:
                add(e.get("id"), e.get("timestamp"), "out", e.get("to"))
    return found[:MAX_MESSAGES_PER_NOTICE]


def receive(db: Session, integration_id: uuid.UUID, signature: str | None, body: bytes) -> dict:
    """A notice from WhatsApp. Returns how many messages it told about and how many were new."""
    row = _integration(db, integration_id)
    secret = stored_tokens(row).refresh_token or ""
    if not _signature_ok(secret, signature, body):
        raise AppError(
            "That message was not signed correctly.", code="bad_signature", status_code=403
        )
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        raise AppError(
            "That message could not be read.", code="bad_message", status_code=400
        ) from exc

    organization_id = row.organization_id
    phone_id = str(row.settings.get("phone_number_id") or row.external_account_id)
    messages = _messages(payload, phone_id, organization_id)
    added = 0
    with tenant_scope(db, organization_id):
        if messages:
            stmt = (
                pg_insert(CustomerMessage)
                .values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "organization_id": organization_id,
                            "source": "whatsapp",
                            "channel": "whatsapp",
                            **m,
                        }  # fmt: skip
                        for m in messages
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "source", "source_ref"],
                    index_where=text("source_ref IS NOT NULL"),
                )
            )
            added = len(db.execute(stmt.returning(CustomerMessage.id)).all())
            integration = db.get(Integration, row.id)
            now = datetime.now(UTC)
            integration.last_sync_at = integration.last_successful_sync_at = now
            integration.last_sync_status = "succeeded"
            integration.last_error_code = integration.last_error_message = None
            integration.last_error_at = None
            integration.consecutive_failures = 0
            if added:
                _refresh_figures(db, integration, now)
        db.commit()
    return {"received": len(messages), "added": added}


def _refresh_figures(db: Session, integration: Integration, now: datetime) -> None:
    """New messages change the figures, so queue working them out again (at most every few
    minutes). Best effort: a notice must never fail because of this."""
    cursor = dict(integration.sync_cursor or {})
    queued = cursor.get("figures_queued_at")
    if queued:
        try:
            if now - datetime.fromisoformat(queued) < FIGURES_EVERY:
                return
        except ValueError:
            pass
    cursor["figures_queued_at"] = now.isoformat()
    integration.sync_cursor = cursor
    owner = SimpleNamespace(
        user=SimpleNamespace(id=integration.connected_by_user_id),
        organization_id=integration.organization_id,
    )
    try:
        enqueue(
            db,
            owner,
            kind="kpi.calculate",
            subject_type="organization",
            subject_id=integration.organization_id,
            meta=RequestMeta(),
            payload={"trigger": "import"},
        )
    except ConflictError:
        pass  # one is already waiting to run; it will see these messages too
    except Exception:
        logger.warning("Could not queue a KPI recalculation", exc_info=True)
