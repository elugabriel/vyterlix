"""The job queue: put work on it, and (in the worker) take work off it and run it.

PostgreSQL is the queue. `claim_next` takes the oldest due job with FOR UPDATE SKIP LOCKED,
so any number of workers can run at once without taking the same job.

Lifecycle: queued -> running -> succeeded | failed. An unexpected error puts the job back to
queued for a later retry (up to max_attempts); an expected one (an AppError such as "check the
data before importing") fails it straight away with the same plain-English message the web
request would have shown. A worker that dies mid-job is noticed by its stale heartbeat.

Times come from Python, not the database's now(), which is frozen for a whole transaction.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.permissions import Perm
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.models.identity import User
from app.models.jobs import Job
from app.services.auth import RequestMeta
from app.services.organizations import resolve_membership
from app.services.storage import FileStorage, get_file_storage

logger = logging.getLogger("vyterlix.jobs")

GENERIC_FAILURE = (
    "Something went wrong on our side, so this didn't finish. Please try again; if it keeps "
    "happening, contact support."
)
GAVE_UP = (
    "This stopped part-way (the background worker was interrupted several times). "
    "Nothing was changed. Please try again."
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JobTenant:
    """Stands in for the web request's Tenant: the services only need who and which business."""

    organization_id: uuid.UUID
    user: User


@dataclass(frozen=True)
class JobContext:
    """Everything a handler needs. `progress(done, total)` is safe to call often."""

    job: Job
    db: Session
    tenant: JobTenant
    meta: RequestMeta
    storage: FileStorage
    progress: Callable[[int, int], None]


@dataclass(frozen=True)
class Handler:
    run: Callable[[JobContext], BaseModel | dict[str, Any] | None]
    permission: Perm


HANDLERS: dict[str, Handler] = {}


def register(kind: str, permission: Perm):
    """Decorator: `@register("import.validate", Perm.DATA_MANAGE)`."""

    def _add(fn):
        HANDLERS[kind] = Handler(fn, permission)
        return fn

    return _add


# --- putting work on the queue (web requests) ------------------------------------------


def enqueue(
    db: Session,
    tenant,
    *,
    kind: str,
    subject_type: str,
    subject_id: uuid.UUID,
    meta: RequestMeta,
    max_attempts: int = 3,
    payload: dict | None = None,
) -> Job:
    """Queue a job. Refused with 409 if the same subject already has one queued or running."""
    if kind not in HANDLERS:
        raise ValueError(f"No handler registered for {kind!r}")
    job = Job(
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        requested_by_user_id=tenant.user.id,
        payload={"ip_address": meta.ip_address, "user_agent": meta.user_agent, **(payload or {})},
        max_attempts=max_attempts,
        created_at=_now(),  # not the database's now(), which is frozen within a transaction
        run_after=_now(),
        status="queued",
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "Something is already running on this upload. Wait for it to finish.",
            code="job_already_running",
        ) from exc
    db.commit()
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)  # tenant scope hides other businesses' jobs
    if job is None:
        raise NotFoundError("Job not found")
    return job


def jobs_for(db: Session, subject_id: uuid.UUID, limit: int = 5) -> list[Job]:
    return list(
        db.scalars(
            select(Job)
            .where(Job.subject_id == subject_id)
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
        )
    )


# --- the worker side ---------------------------------------------------------------------


def claim_next(db: Session, worker_id: str, *, now: datetime | None = None) -> Job | None:
    """Take the oldest job that is due, mark it running, and commit (releasing the row lock)."""
    now = now or _now()
    job = db.scalars(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= now)
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(**ACROSS_TENANTS)
    ).first()
    if job is None:
        db.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_by = worker_id
    job.started_at = now
    job.heartbeat_at = now
    job.error_code = job.error_message = None
    job.progress_done = 0
    job_id = job.id
    db.commit()
    return _reload(db, job_id)


def recover_stale(db: Session, *, now: datetime | None = None) -> int:
    """Running jobs whose worker went silent: queue them again, or fail them if out of tries."""
    now = now or _now()
    cutoff = now - timedelta(seconds=get_settings().job_stale_after_seconds)
    stale = db.scalars(
        select(Job)
        .where(Job.status == "running", Job.heartbeat_at < cutoff)
        .with_for_update(skip_locked=True)
        .execution_options(**ACROSS_TENANTS)
    ).all()
    for job in stale:
        if job.attempts < job.max_attempts:
            _requeue(job, now, GENERIC_FAILURE, "worker_lost")
        else:
            _fail(job, now, GAVE_UP, "worker_lost")
    db.commit()
    return len(stale)


def _requeue(job: Job, now: datetime, message: str, code: str) -> None:
    delay = get_settings().job_retry_delay_seconds * job.attempts
    job.status, job.run_after = "queued", now + timedelta(seconds=delay)
    job.locked_by = None
    job.error_code, job.error_message = code, message


def _fail(job: Job, now: datetime, message: str, code: str) -> None:
    job.status, job.finished_at = "failed", now
    job.error_code, job.error_message = code, message


def _authorise(db: Session, job: Job, permission: Perm) -> JobTenant:
    """The person who asked must still be allowed to: they may have left or been demoted."""
    user = db.get(User, job.requested_by_user_id) if job.requested_by_user_id else None
    if user is None:
        raise AppError(
            "The person who started this is no longer on the account.", code="no_requester"
        )
    try:
        membership = resolve_membership(db, user, job.organization_id)
    except AppError as exc:
        raise AppError(
            "You no longer have access to this business.", code="access_removed"
        ) from exc
    if permission not in membership.permissions:
        raise AppError("Your role no longer allows this.", code="permission_removed")
    return JobTenant(organization_id=job.organization_id, user=user)


def _progress_reporter(
    job: Job, db: Session, own_session: Callable[[], Session] | None
) -> Callable[[int, int], None]:
    """Writes progress and a heartbeat. With `own_session` (the real worker) it commits on its
    own connection, so the browser sees progress while the main transaction is still open."""

    def report(done: int, total: int) -> None:
        values = {"progress_done": done, "progress_total": total, "heartbeat_at": _now()}
        statement = update(Job).where(Job.id == job.id).values(**values)
        statement = statement.execution_options(**ACROSS_TENANTS)
        if own_session is None:
            db.execute(statement)
            return
        with own_session() as other:
            other.execute(statement)
            other.commit()

    return report


def _reload(db: Session, job_id: uuid.UUID) -> Job:
    return db.scalars(
        select(Job)
        .where(Job.id == job_id)
        .execution_options(populate_existing=True, **ACROSS_TENANTS)
    ).one()


def run_job(
    db: Session,
    job: Job,
    *,
    storage: FileStorage | None = None,
    own_session: Callable[[], Session] | None = None,
) -> Job:
    """Run a claimed job and record how it went. Never raises for a failing job."""
    storage = storage or get_file_storage()
    job_id = job.id
    job = _reload(db, job_id)  # a commit may have expired it (sessions differ in expire_on_commit)
    kind = job.kind
    handler = HANDLERS.get(job.kind)
    outcome: BaseModel | dict | None = None
    error: AppError | None = None
    unexpected = False
    try:
        if handler is None:
            raise AppError(f"This kind of job ({job.kind}) isn't supported.", code="unknown_kind")
        with tenant_scope(db, job.organization_id):
            tenant = _authorise(db, job, handler.permission)
            meta = RequestMeta(
                ip_address=job.payload.get("ip_address"), user_agent=job.payload.get("user_agent")
            )
            context = JobContext(
                job, db, tenant, meta, storage, _progress_reporter(job, db, own_session)
            )
            outcome = handler.run(context)
    except AppError as exc:
        db.rollback()
        error = exc
    except Exception:
        db.rollback()
        logger.exception("Job %s (%s) crashed", job_id, kind)
        unexpected = True

    now = _now()
    job = _reload(db, job_id)
    if error is None and not unexpected:
        job.status, job.finished_at, job.locked_by = "succeeded", now, None
        job.result = outcome.model_dump(mode="json") if isinstance(outcome, BaseModel) else outcome
        job.progress_done = job.progress_total = max(job.progress_total, job.progress_done)
        job.error_code = job.error_message = None
    elif error is not None:
        _fail(job, now, error.message, error.code)
        job.locked_by = None
    elif job.attempts < job.max_attempts:
        _requeue(job, now, GENERIC_FAILURE, "internal_error")
    else:
        _fail(job, now, GENERIC_FAILURE, "internal_error")
        job.locked_by = None
    job_id = job.id
    db.commit()
    return _reload(db, job_id)


def work_once(
    db: Session,
    worker_id: str,
    *,
    storage: FileStorage | None = None,
    own_session: Callable[[], Session] | None = None,
) -> Job | None:
    """Recover lost jobs, then run at most one job. Returns it, or None if nothing was due."""
    recover_stale(db)
    job = claim_next(db, worker_id)
    if job is None:
        return None
    return run_job(db, job, storage=storage, own_session=own_session)


def prune_finished(db: Session, *, now: datetime | None = None) -> int:
    """Delete finished jobs older than job_keep_days (the import records themselves stay)."""
    now = now or _now()
    cutoff = now - timedelta(days=get_settings().job_keep_days)
    result = db.execute(
        Job.__table__.delete()
        .where(Job.status.in_(("succeeded", "failed")), Job.finished_at < cutoff)
        .execution_options(**ACROSS_TENANTS)
    )
    db.commit()
    return result.rowcount
