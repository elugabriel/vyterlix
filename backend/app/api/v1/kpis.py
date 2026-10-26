from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.jobs import JobOut
from app.schemas.kpi import (
    BreakdownOut,
    FollowUpOut,
    Granularity,
    KpiHistoryOut,
    KpisOut,
    MoversOut,
    ProjectsOut,
    ServicesOut,
    TeamOut,
)
from app.services import job_handlers  # noqa: F401  (registers the handlers)
from app.services.jobs import enqueue
from app.services.kpi import kpi_history, list_kpis
from app.services.kpi_breakdown import (
    breakdown,
    customers_to_follow_up,
    movers,
    popular_services,
    project_profit,
    team_workload,
)

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


@router.get("/breakdown/{dimension}", response_model=BreakdownOut)
def sales_breakdown(
    dimension: Literal["channel", "product"],
    tenant: Viewer,
    db: DB,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Where sales come from, by sales channel or by product, for a date range (default: the last
    12 finished months). The biggest come first; the rest are rolled into "Everything else"."""
    return breakdown(db, dimension, date_from, date_to, limit)


@router.get("/services", response_model=ServicesOut)
def services_booked(tenant: Viewer, db: DB, limit: Annotated[int, Query(ge=1, le=25)] = 10):
    """The services people book, most popular first, and which ones people do not come back for."""
    return popular_services(db, limit=limit)


@router.get("/projects", response_model=ProjectsOut)
def projects_profit(tenant: Viewer, db: DB):
    """What each project has earned and cost, the ones losing money first."""
    return project_profit(db)


@router.get("/team", response_model=TeamOut)
def team(tenant: Viewer, db: DB):
    """Who did the appointments over the last three months, and how they went."""
    return team_workload(db)


@router.get("/customers/follow-up", response_model=FollowUpOut)
def customers_to_follow_up_list(
    tenant: Viewer,
    db: DB,
    quiet_days: Annotated[int, Query(ge=14, le=365)] = 60,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
):
    """Customers who used to buy and have gone quiet, the ones who spent most first."""
    return customers_to_follow_up(db, quiet_days=quiet_days, limit=limit)


@router.get("/stock/movers", response_model=MoversOut)
def stock_movers(
    tenant: Viewer,
    db: DB,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
):
    """Fast movers, slow movers and dead stock (in stock, but not sold in the last `days` days)."""
    return movers(db, days=days, limit=limit)


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
