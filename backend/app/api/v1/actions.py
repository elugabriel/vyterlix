import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.actions import (
    ActionCountsOut,
    ActionOut,
    ActionPatch,
    ActionStatus,
    ActionSummaryOut,
    EvidenceIn,
    NoteIn,
    RejectIn,
    StatusIn,
)
from app.schemas.outcomes import OutcomesSummaryOut, ReportOut
from app.services import actions as service
from app.services import outcomes
from app.services.storage import FileStorage, get_file_storage

router = APIRouter(prefix="/organizations/{organization_id}/actions", tags=["actions"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
# Owners and Managers; the service also checks a Manager's remit for the action's area.
Worker = Annotated[Tenant, Depends(require_permission(Perm.ACTIONS_MANAGE))]
Approver = Annotated[Tenant, Depends(require_permission(Perm.RECOMMENDATIONS_ACTION))]
Storage = Annotated[FileStorage, Depends(get_file_storage)]


@router.get("", response_model=list[ActionSummaryOut])
def actions(
    tenant: Viewer,
    db: DB,
    status: ActionStatus | None = None,
    mine: bool = False,
    open_only: bool = False,
    owner: uuid.UUID | None = None,
):
    """The business's actions: overdue first, then by target date. `mine` is the ones assigned to
    you; `open_only` leaves out finished and cancelled ones."""
    return service.list_actions(
        db, tenant, status=status, owner_user_id=owner, mine=mine, open_only=open_only
    )


@router.get("/summary", response_model=ActionCountsOut)
def summary(tenant: Viewer, db: DB):
    """How many actions are in each state, how many are due soon, and how many are yours."""
    return service.counts(db, tenant)


@router.get("/outcomes", response_model=OutcomesSummaryOut)
def outcome_summary(tenant: Viewer, db: DB):
    """How many finished actions are waiting to be checked, how the checked ones turned out, and
    how each kind of action has worked for this business."""
    return outcomes.summary(db)


@router.get("/{action_id}", response_model=ActionOut)
def one(action_id: uuid.UUID, tenant: Viewer, db: DB):
    """One action with its history (oldest first) and evidence."""
    return service.get_action(db, action_id)


@router.patch("/{action_id}", response_model=ActionOut)
def change(action_id: uuid.UUID, body: ActionPatch, tenant: Worker, db: DB, meta: Meta):
    """Rename it, change the steps (tick them off), give it to someone, or move its dates."""
    return service.update(db, tenant, action_id, body, meta)


@router.post("/{action_id}/status", response_model=ActionOut)
def status(action_id: uuid.UUID, body: StatusIn, tenant: Worker, db: DB, meta: Meta):
    """Move it forward: in progress, partly done, done, or cancelled. The system sets overdue."""
    return service.set_status(db, tenant, action_id, body, meta)


@router.post("/{action_id}/notes", response_model=ActionOut)
def note(action_id: uuid.UUID, body: NoteIn, tenant: Worker, db: DB):
    return service.add_note(db, tenant, action_id, body.note)


@router.post("/{action_id}/approve", response_model=ActionOut)
def approve(
    action_id: uuid.UUID, tenant: Approver, db: DB, meta: Meta, body: RejectIn | None = None
):
    """Approve an action that was proposed by someone outside its area."""
    return service.approve(db, tenant, action_id, None if body is None else body.reason, meta)


@router.post("/{action_id}/reject", response_model=ActionOut)
def reject(
    action_id: uuid.UUID, tenant: Approver, db: DB, meta: Meta, body: RejectIn | None = None
):
    """Turn down a proposed action. The recommendation can be taken up again."""
    return service.reject(db, tenant, action_id, None if body is None else body.reason, meta)


@router.get("/{action_id}/report", response_model=ReportOut)
def report(action_id: uuid.UUID, tenant: Viewer, db: DB):
    """One action's whole story: what was decided and why, what was done, and what came of it."""
    return outcomes.report(db, action_id)


@router.post("/{action_id}/measure", response_model=ActionOut)
def measure(action_id: uuid.UUID, tenant: Worker, db: DB):
    """Check the result now (once the follow-up date has come and the figures are in)."""
    return service.measure_now(db, tenant, action_id)


@router.post("/{action_id}/evidence", response_model=ActionOut)
def evidence(action_id: uuid.UUID, body: EvidenceIn, tenant: Worker, db: DB, meta: Meta):
    """Attach a note or a link to show the work."""
    return service.add_evidence(db, tenant, action_id, body, meta)


@router.post("/{action_id}/evidence/file", response_model=ActionOut)
def evidence_file(
    action_id: uuid.UUID,
    tenant: Worker,
    db: DB,
    meta: Meta,
    storage: Storage,
    file: Annotated[
        UploadFile, File(description="A picture, pdf, txt, csv, xlsx or docx, up to 5 MB")
    ],
    title: Annotated[str | None, Form(max_length=200)] = None,
):
    """Attach a file to show the work."""
    return service.add_file(
        db, storage, tenant, action_id, stream=file.file, filename=file.filename or "file",
        title=title, meta=meta,
    )  # fmt: skip


@router.get("/{action_id}/evidence/{evidence_id}/file")
def download(
    action_id: uuid.UUID, evidence_id: uuid.UUID, tenant: Viewer, db: DB, storage: Storage
):
    """Download an attached file (always as a download, never shown in the page)."""
    item, stream = service.evidence_file(db, storage, action_id, evidence_id)
    return StreamingResponse(
        stream,
        media_type=item.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{item.original_filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
