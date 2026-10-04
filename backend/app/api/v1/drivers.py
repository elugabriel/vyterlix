from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.diagnostics import DriversOut
from app.services.drivers import explain

router = APIRouter(prefix="/organizations/{organization_id}/drivers", tags=["diagnostics"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("/{metric}", response_model=DriversOut)
def drivers(
    metric: str,
    month: date,
    tenant: Viewer,
    db: DB,
    against: Literal["previous_month", "last_year"] = "previous_month",
):
    """What drove a change: the figure for `month` against the month before (or the same month
    last year), read through lenses that each split the change in two (the days in the month,
    the number and size of sales, items sold and prices charged), plus any one product, channel,
    customer, day or cost that accounts for much of it. `metric` is a KPI code (see
    /segments for the figures that can be explained)."""
    return explain(db, metric, month, against)
