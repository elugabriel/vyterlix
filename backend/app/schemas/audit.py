import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditActor(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str


class AuditEntryOut(BaseModel):
    id: uuid.UUID
    action: str
    actor: AuditActor | None  # None if the user has since been deleted, or for system events
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    details: dict[str, Any] | None
    created_at: datetime


class AuditLogPage(BaseModel):
    entries: list[AuditEntryOut]
    # Pass as `before` to fetch the next (older) page; None when there are no more.
    next_before: uuid.UUID | None
