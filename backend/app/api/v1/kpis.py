from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.jobs import JobOut
from app.schemas.kpi import Granularity, KpiHistoryOut, KpisOut
from app.services import job_handlers  # noqa: F401  (registers the handlers)
from app.services.jobs import enqueue
from app.services.kpi import kpi_history, list_kpis

router = APIRouter(prefix="/organizations/{organization_id}/kpis", tags=["kpis"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]


@router.get("", response_model=KpisOut)
def kpis(tenant: Viewer, db: DB, granularity: Granularity = "month"):
    """Every KPI with its latest finished period and the period in progress."""
    return list_kpis(db, granularity)


@router.post("/calculate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
def calculate(
    tenant: DataManager, db: DB, meta: Meta, granularity: Annotated[Granularity, Query()] = "month"
):
    """Work every KPI out again, in the background (needs the worker). They are also
    recalculated automatically after an import or an undo."""
    return enqueue(
        db,
        tenant,
        kind="kpi.calculate",
        subject_type="organization",
        subject_id=tenant.organization_id,
        meta=meta,
        payload={"granularity": granularity, "trigger": "manual"},
    )


@router.get("/{code}", response_model=KpiHistoryOut)
def history(
    code: str,
    tenant: Viewer,
    db: DB,
    granularity: Granularity = "month",
    limit: Annotated[int, Query(ge=1, le=120)] = 24,
):
    """One KPI over time, oldest first, with the formula it is worked out from."""
    return kpi_history(db, code, granularity, limit)
