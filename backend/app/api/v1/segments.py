from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.diagnostics import SegmentOptionsOut, SegmentOut
from app.services.segments import available, segment_change

router = APIRouter(prefix="/organizations/{organization_id}/segments", tags=["diagnostics"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("", response_model=SegmentOptionsOut)
def options(tenant: Viewer):
    """Which figures can be split into parts, and by what (the figure is a KPI code)."""
    return SegmentOptionsOut(metrics=available())


@router.get("/{metric}/{dimension}", response_model=SegmentOut)
def split(
    metric: str,
    dimension: str,
    month: date,
    tenant: Viewer,
    db: DB,
    against: Literal["previous_month", "last_year"] = "previous_month",
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
):
    """Where a change came from: the figure for `month` split by product, channel, customer, day
    of the week, cost category or supplier, each part compared with the month before (or the same
    month last year), biggest movers first."""
    return segment_change(db, metric, dimension, month, against, limit)
