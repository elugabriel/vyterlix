from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.health import HealthHistoryOut, HealthOut
from app.services.health import health_for_month, health_history, latest_health

router = APIRouter(prefix="/organizations/{organization_id}/business-health", tags=["health"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("", response_model=HealthOut | None)
def latest(tenant: Viewer, db: DB):
    """The business's health for the latest finished month, with every area and the evidence
    behind it. Null until the KPIs have been worked out (they are, after every import)."""
    return latest_health(db)


@router.get("/history", response_model=HealthHistoryOut)
def history(tenant: Viewer, db: DB, limit: Annotated[int, Query(ge=1, le=120)] = 24):
    """The overall score for each finished month, oldest first."""
    return health_history(db, limit)


@router.get("/{month}", response_model=HealthOut)
def for_month(month: date, tenant: Viewer, db: DB):
    """One finished month (give its first day, e.g. 2026-02-01)."""
    return health_for_month(db, month.replace(day=1))
