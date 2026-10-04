from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.forecast import AccuracyOut, ForecastOptionsOut, ForecastOut
from app.services.forecast import (
    DEFAULT_HORIZON,
    accuracy,
    calculate,
    forecastable,
    read_latest,
)

router = APIRouter(prefix="/organizations/{organization_id}/forecasts", tags=["forecasts"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Manager = Annotated[Tenant, Depends(require_permission(Perm.ACTIONS_MANAGE))]


@router.get("", response_model=ForecastOptionsOut)
def options(tenant: Viewer):
    """Which figures can be forecast (each is a KPI code)."""
    return ForecastOptionsOut(kpis=forecastable())


@router.get("/{kpi_code}/accuracy", response_model=AccuracyOut)
def how_accurate(kpi_code: str, tenant: Viewer, db: DB):
    """How well past forecasts of a figure matched what really happened: how often the real figure
    landed inside the range, and the typical miss, over every forecast month that has finished."""
    return accuracy(db, kpi_code)


@router.get("/{kpi_code}", response_model=ForecastOut | None)
def latest(kpi_code: str, tenant: Viewer, db: DB):
    """The latest forecast of a figure, with a range around every month. Null until the figures
    have been worked out (they are, after every import)."""
    return read_latest(db, kpi_code)


@router.post("/{kpi_code}", response_model=ForecastOut)
def make(
    kpi_code: str,
    tenant: Manager,
    db: DB,
    horizon: Annotated[int, Query(ge=1, le=12)] = DEFAULT_HORIZON,
    level: Annotated[int, Query(description="How sure the range is, in per cent")] = 80,
):
    """Forecast a figure for the next `horizon` months now. Safe to run again: a forecast made
    from the same last month is replaced."""
    return calculate(db, tenant, kpi_code, horizon, level)
