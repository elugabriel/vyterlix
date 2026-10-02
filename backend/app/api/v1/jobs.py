import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.jobs import ImportJobIn, JobOut
from app.services import job_handlers  # noqa: F401  (registers the handlers)
from app.services.imports import get_record
from app.services.jobs import enqueue, get_job, jobs_for

DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]

router = APIRouter(prefix="/organizations/{organization_id}", tags=["jobs"])


@router.post(
    "/imports/{import_id}/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut
)
def start(import_id: uuid.UUID, body: ImportJobIn, tenant: DataManager, db: DB, meta: Meta):
    """Check, import or undo an upload in the background. Returns straight away; poll the job.

    Needs the worker running (`python -m app.cli.worker run`). One job at a time per upload."""
    get_record(db, import_id)  # 404 if it isn't this business's
    return enqueue(
        db,
        tenant,
        kind=job_handlers.IMPORT_ACTIONS[body.action],
        subject_type="data_import",
        subject_id=import_id,
        meta=meta,
    )


@router.get("/imports/{import_id}/jobs", response_model=list[JobOut])
def for_import(import_id: uuid.UUID, tenant: DataManager, db: DB):
    """The latest jobs for an upload, newest first (so a reloaded page finds the running one)."""
    get_record(db, import_id)
    return jobs_for(db, import_id)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get(job_id: uuid.UUID, tenant: DataManager, db: DB):
    return get_job(db, job_id)
