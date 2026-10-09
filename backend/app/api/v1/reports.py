import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.reports import (
    MonthsIn,
    ReportOut,
    RunOut,
    RunSummaryOut,
    ScheduleIn,
    ScheduleOut,
    SchedulePatch,
)
from app.services import reports as service

router = APIRouter(prefix="/organizations/{organization_id}/reports", tags=["reports"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Worker = Annotated[Tenant, Depends(require_permission(Perm.ACTIONS_MANAGE))]
Owner = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=list[ReportOut])
def reports(tenant: Viewer, db: DB):
    """The reports this business has, the latest one written for you, and how each is scheduled."""
    return service.list_reports(db, tenant)


@router.get("/runs", response_model=list[RunSummaryOut])
def runs(
    tenant: Viewer,
    db: DB,
    report_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    """The reports written for you, newest first. Nobody else can see them."""
    return service.list_runs(db, tenant, report_id, limit)


@router.get("/runs/{run_id}", response_model=RunOut)
def run(run_id: uuid.UUID, tenant: Viewer, db: DB):
    return service.get_run(db, tenant, run_id)


def _download(data: bytes, name: str, ctype: str) -> Response:
    return Response(
        content=data,
        media_type=ctype,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/runs/{run_id}/pdf")
def pdf(run_id: uuid.UUID, tenant: Viewer, db: DB, meta: Meta):
    return _download(*service.export(db, tenant, run_id, "pdf", meta))


@router.get("/runs/{run_id}/csv")
def csv(run_id: uuid.UUID, tenant: Viewer, db: DB, meta: Meta):
    return _download(*service.export(db, tenant, run_id, "csv", meta))


@router.get("/schedules", response_model=list[ScheduleOut])
def schedules(tenant: Worker, db: DB):
    return service.list_schedules(db)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: uuid.UUID, body: SchedulePatch, tenant: Owner, db: DB, meta: Meta):
    return service.update_schedule(db, tenant, schedule_id, body, meta)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: uuid.UUID, tenant: Owner, db: DB, meta: Meta):
    service.delete_schedule(db, tenant, schedule_id, meta)


@router.put("/{report_id}/months", response_model=ReportOut)
def months(report_id: uuid.UUID, body: MonthsIn, tenant: Owner, db: DB):
    """How many months a report covers."""
    return service.set_months(db, tenant, report_id, body)


@router.post("/{report_id}/generate", response_model=RunOut)
def generate(report_id: uuid.UUID, tenant: Viewer, db: DB, meta: Meta):
    """Write the report now, for you."""
    return service.generate_now(db, tenant, report_id, meta)


@router.post(
    "/{report_id}/schedules", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED
)
def create_schedule(report_id: uuid.UUID, body: ScheduleIn, tenant: Owner, db: DB, meta: Meta):
    """Have the report written and emailed weekly or monthly to the people you choose."""
    return service.create_schedule(db, tenant, report_id, body, meta)
