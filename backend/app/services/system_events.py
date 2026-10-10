"""Things that went wrong in the running system, kept so platform staff can see them (Phase 17).

A short plain-English message we write ourselves, plus a few safe facts (a kind of work, an error
code). Never a stack trace, a secret or anything from inside a business.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.admin import SystemEvent


def record(
    db: Session,
    kind: str,
    message: str,
    *,
    severity: str = "error",
    organization_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an event to the current transaction (the caller commits)."""
    db.add(
        SystemEvent(
            kind=kind,
            severity=severity,
            message=message[:300],
            organization_id=organization_id,
            details=details,
        )
    )
