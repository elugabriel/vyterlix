import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.identity import AuditLog


def record_audit(
    db: Session,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the current transaction. Never put secrets in `details`."""
    db.add(
        AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,
            details=details,
        )
    )
