import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.diagnostics import DetectionOut
from app.services.detection import get_event, list_events

router = APIRouter(prefix="/organizations/{organization_id}/changes", tags=["diagnostics"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("", response_model=list[DetectionOut])
def changes(
    tenant: Viewer,
    db: DB,
    month: date | None = None,
    effect: Literal["good", "bad", "neutral"] | None = None,
    severity: Literal["notable", "major"] | None = None,
    include_expected: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """The changes in the business's figures that are bigger than normal, newest month first and
    the biggest first within a month. `month` is the first day of a month (e.g. 2026-02-01)."""
    return list_events(
        db,
        month=None if month is None else month.replace(day=1),
        effect=effect,
        severity=severity,
        include_expected=include_expected,
        limit=limit,
    )


@router.get("/{event_id}", response_model=DetectionOut)
def one_change(event_id: uuid.UUID, tenant: Viewer, db: DB):
    return get_event(db, event_id)
