"""What each kind of job does. Importing this module registers them (see services/jobs.py)."""

import logging

from pydantic import BaseModel

from app.core.errors import ConflictError
from app.core.permissions import Perm
from app.services import actions, alerts, detection, forecast, health, kpi, memory, outcomes
from app.services.import_runner import run_import, undo_import
from app.services.import_validation import validate_import
from app.services.integrations import run_sync
from app.services.jobs import JobContext, enqueue, register

logger = logging.getLogger("vyterlix.jobs")

# What the API accepts as an "action" on an import, and the job kind that runs it.
IMPORT_ACTIONS = {
    "validate": "import.validate",
    "import": "import.run",
    "undo": "import.undo",
}


@register("import.validate", Perm.DATA_MANAGE)
def validate(ctx: JobContext) -> BaseModel:
    return validate_import(
        ctx.db, ctx.storage, ctx.tenant, ctx.job.subject_id, ctx.meta, progress=ctx.progress
    )


def _refresh_kpis_later(ctx: JobContext) -> None:
    """The business's data just changed, so its KPIs are out of date: queue a recalculation.
    Best effort: it must never make the import or undo that already succeeded look failed."""
    try:
        enqueue(
            ctx.db,
            ctx.tenant,
            kind="kpi.calculate",
            subject_type="organization",
            subject_id=ctx.tenant.organization_id,
            meta=ctx.meta,
            payload={"trigger": "import"},
        )
    except ConflictError:
        pass  # one is already waiting to run; it will see this change too
    except Exception:
        logger.warning("Could not queue a KPI recalculation", exc_info=True)


@register("import.run", Perm.DATA_MANAGE)
def import_rows(ctx: JobContext) -> BaseModel:
    result = run_import(ctx.db, ctx.tenant, ctx.job.subject_id, ctx.meta, progress=ctx.progress)
    _refresh_kpis_later(ctx)
    return result


@register("import.undo", Perm.DATA_MANAGE)
def undo(ctx: JobContext) -> BaseModel:
    result = undo_import(ctx.db, ctx.tenant, ctx.job.subject_id, ctx.meta)
    _refresh_kpis_later(ctx)
    return result


@register("integration.sync", Perm.DATA_MANAGE)
def sync_integration(ctx: JobContext) -> dict:
    return run_sync(ctx)


@register("kpi.calculate", Perm.DATA_MANAGE)
def calculate_kpis(ctx: JobContext) -> BaseModel:
    payload = ctx.job.payload
    run = kpi.calculate(
        ctx.db,
        ctx.tenant,
        granularity=payload.get("granularity", "month"),
        trigger=payload.get("trigger", "manual"),
        job_id=ctx.job.id,
    )
    # The health score is built from the KPIs just stored, so refresh it straight away. A
    # failure here must not hide that the KPIs themselves were worked out.
    try:
        health.calculate(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not work out business health", exc_info=True)
    # Likewise the changes worth a look are found from the KPIs just stored.
    try:
        detection.detect(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not look for changes in the figures", exc_info=True)
    # And the forecast, which learns from the same figures.
    try:
        forecast.calculate_all(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not forecast the figures", exc_info=True)
    # Anything in the new figures that needs attention becomes an alert.
    try:
        alerts.evaluate(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not look for things that need attention", exc_info=True)
    # What is normal for the business is worked out again from the new figures.
    try:
        memory.rebuild(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not update what we know about the business", exc_info=True)
    # Work that has run past its date is marked overdue.
    try:
        actions.refresh_overdue(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not check for overdue actions", exc_info=True)
    # New figures may be the ones a finished action was waiting for.
    try:
        outcomes.sweep(ctx.db, ctx.tenant)
    except Exception:
        ctx.db.rollback()
        logger.error("Could not follow up finished actions", exc_info=True)
    return run
