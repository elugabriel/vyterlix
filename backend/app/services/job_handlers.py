"""What each kind of job does. Importing this module registers them (see services/jobs.py)."""

from pydantic import BaseModel

from app.core.permissions import Perm
from app.services.import_runner import run_import, undo_import
from app.services.import_validation import validate_import
from app.services.integrations import run_sync
from app.services.jobs import JobContext, register

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


@register("import.run", Perm.DATA_MANAGE)
def import_rows(ctx: JobContext) -> BaseModel:
    return run_import(ctx.db, ctx.tenant, ctx.job.subject_id, ctx.meta, progress=ctx.progress)


@register("import.undo", Perm.DATA_MANAGE)
def undo(ctx: JobContext) -> BaseModel:
    return undo_import(ctx.db, ctx.tenant, ctx.job.subject_id, ctx.meta)


@register("integration.sync", Perm.DATA_MANAGE)
def sync_integration(ctx: JobContext) -> dict:
    return run_sync(ctx)
