# ruff: noqa: E501  (some blocks are laid out as columns for reading)
"""Actions: turning a recommendation into work, and following the work to the end.

`accept` makes the decision (an intervention, kept with the figure's level at the time and what it
was expected to win back) and the work (an action with an owner, dates and steps). A Manager acting
outside their remit can only propose: the action waits as pending until the owner (or a Manager
whose remit covers it) approves it. From then on the action moves forward by people's choice,
every change is written to its history, and the system marks it overdue when the target date
passes. Evidence (notes, links, files) can be attached to show the work.

The rules for what an action can become are in app/actions/rules.py.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import PurePath
from typing import BinaryIO

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.actions import rules
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import KpiCategory, Perm
from app.core.uk import today_uk
from app.integrations.base import utcnow
from app.models.actions import (
    ActionEvidence,
    ActionUpdate,
    BusinessAction,
    BusinessIntervention,
)
from app.models.diagnostics import DetectionEvent
from app.models.identity import OrganizationUser, User
from app.models.kpi import KpiDefinition
from app.models.recommendations import Intervention, Recommendation, RecommendationOption
from app.schemas.actions import (
    AcceptIn,
    ActionCountsOut,
    ActionOut,
    ActionPatch,
    ActionSummaryOut,
    DismissIn,
    EvidenceIn,
    EvidenceOut,
    PersonOut,
    ProgressOut,
    StatusIn,
    StepOut,
    UpdateOut,
    WhatWasDecidedOut,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta
from app.services.storage import FileStorage, FileTooLargeError, evidence_key

MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
ALLOWED_SUFFIXES = {"png", "jpg", "jpeg", "pdf", "txt", "csv", "xlsx", "docx"}
CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# --- small helpers ------------------------------------------------------------------------------


def _people(db: Session, ids: set) -> dict[uuid.UUID, PersonOut]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()
    return {i: PersonOut(id=i, name=name) for i, name in rows}


def _record(
    db: Session,
    tenant,
    action: BusinessAction,
    kind: str,
    *,
    note: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict | None = None,
    system: bool = False,
) -> None:
    now = utcnow()
    if action.last_activity_at is not None and now <= action.last_activity_at:
        now = action.last_activity_at + timedelta(microseconds=1)  # keeps the history in order
    db.add(
        ActionUpdate(
            organization_id=tenant.organization_id,
            action_id=action.id,
            user_id=None if system else tenant.user.id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            note=note,
            details=details or {},
            created_at=now,
        )
    )
    action.last_activity_at = now


def _audit(db: Session, tenant, meta: RequestMeta, what: AuditAction, action_id, **details) -> None:
    record_audit(
        db,
        what,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="action",
        target_id=action_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details=details,
    )


def _member(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    """The person must belong to this business to be given its work."""
    found = db.scalar(
        select(OrganizationUser.user_id).where(
            OrganizationUser.user_id == user_id, OrganizationUser.status == "active"
        )
    )
    if found is None:
        raise AppError(
            "That person is not a member of this business.", code="not_a_member", status_code=422
        )
    return found


def _category(value: str) -> KpiCategory:
    return KpiCategory(value)


def _require(tenant, permission: Perm, category: str) -> None:
    if not tenant.can(permission, _category(category)):
        raise PermissionDeniedError(
            f"This is about {category}, which is outside your remit",
            code="outside_remit",
            details={"category": category},
        )


def _get(db: Session, action_id: uuid.UUID) -> BusinessAction:
    found = db.get(BusinessAction, action_id)
    if found is None:
        raise NotFoundError("That action was not found", code="action_not_found")
    return found


def _closed() -> ConflictError:
    return ConflictError(
        "This action is finished (done or cancelled) and can no longer be changed.",
        code="action_closed",
    )


def _words(value: date | None) -> str:
    return "none" if value is None else f"{value:%d/%m/%Y}"


# --- accepting -----------------------------------------------------------------------------------


def accept(
    db: Session, tenant, event_id: uuid.UUID, body: AcceptIn, meta: RequestMeta
) -> ActionOut:
    recommendation = db.scalars(
        select(Recommendation).where(Recommendation.event_id == event_id)
    ).first()
    if recommendation is None:
        raise NotFoundError(
            "Nothing has been recommended for that change yet", code="recommendation_not_found"
        )
    if recommendation.status != "open":
        raise ConflictError(
            f"This recommendation has already been dealt with ({recommendation.status}).",
            code="not_open",
        )
    event = db.get(DetectionEvent, event_id)
    kpi = db.get(KpiDefinition, event.kpi_id)
    options = db.scalars(
        select(RecommendationOption)
        .where(RecommendationOption.recommendation_id == recommendation.id)
        .order_by(RecommendationOption.rank)
    ).all()
    if body.option_id is None:
        option = options[0]
    else:
        option = next((o for o in options if o.id == body.option_id), None)
        if option is None:
            raise NotFoundError(
                "That option is not one of the suggestions", code="option_not_found"
            )
    library = db.get(Intervention, option.intervention_id)

    outright = tenant.can(Perm.RECOMMENDATIONS_ACTION, _category(kpi.category))
    status = "accepted" if outright else "pending"  # outside your remit you can only propose

    today = today_uk()
    start = body.start_date or today
    target = body.target_date or rules.default_target(start, option.days_to_effect)
    if not rules.dates_ok(start, target):
        raise AppError(
            "The target date is before the start date.", code="dates_out_of_order", status_code=422
        )
    owner = (
        _member(db, body.owner_user_id)
        if body.owner_user_id
        else (tenant.user.id if outright else None)
    )

    title = body.title or option.title
    description = body.description or option.description
    steps = (
        rules.clean_steps([s.model_dump() for s in body.steps])
        if body.steps is not None
        else rules.clean_steps(list(library.steps))
    )
    modified = (
        title != option.title
        or description != option.description
        or (body.steps is not None and [s["text"] for s in steps] != list(library.steps))
    )
    now = utcnow()
    intervention = BusinessIntervention(
        organization_id=tenant.organization_id,
        recommendation_id=recommendation.id,
        event_id=event.id,
        library_id=library.id,
        library_code=library.code,
        kpi_id=kpi.id,
        category=kpi.category,
        title=title,
        original_title=option.title if title != option.title else None,
        description=description,
        target_label=option.target_label,
        expected_impact_value=option.impact_value,
        expected_impact_unit=option.impact_unit,
        baseline_period=event.period_start,
        baseline_value=event.value,
        recommendation_score=option.total_score,
        rules_version=recommendation.rules_version,
        basis={
            "headline": recommendation.headline,
            "rationale": recommendation.rationale,
            "scores": option.breakdown,
            "option_rank": option.rank,
            "modified": modified,
        },
        accepted_by_user_id=tenant.user.id,
        accepted_at=now,
    )
    db.add(intervention)
    db.flush()
    action = BusinessAction(
        organization_id=tenant.organization_id,
        intervention_id=intervention.id,
        title=title,
        description=description,
        category=kpi.category,
        steps=steps,
        status=status,
        owner_user_id=owner,
        created_by_user_id=tenant.user.id,
        approved_by_user_id=tenant.user.id if outright else None,
        start_date=start,
        target_date=target,
        last_activity_at=now,
    )
    db.add(action)
    db.flush()
    verb = "Accepted" if outright else "Proposed"
    _record(
        db, tenant, action, "created", to_status=status,
        note=body.note or f"{verb} from the recommendation: {option.title}.",
        details={"option_rank": option.rank, "score": option.total_score},
    )  # fmt: skip
    if modified:
        _record(
            db, tenant, action, "modification",
            note="Changed before accepting.", details={"original_title": option.title},
        )  # fmt: skip
    recommendation.status = "accepted" if outright else "proposed"
    _audit(
        db, tenant, meta,
        AuditAction.ACTION_ACCEPTED if outright else AuditAction.ACTION_PROPOSED,
        action.id, title=title, modified=modified,
    )  # fmt: skip
    db.commit()
    return get_action(db, action.id)


def dismiss(db: Session, tenant, event_id: uuid.UUID, body: DismissIn, meta: RequestMeta):
    """The owner decides not to act on a recommendation. It stays on record, with the reason."""
    recommendation = db.scalars(
        select(Recommendation).where(Recommendation.event_id == event_id)
    ).first()
    if recommendation is None:
        raise NotFoundError(
            "Nothing has been recommended for that change yet", code="recommendation_not_found"
        )
    event = db.get(DetectionEvent, event_id)
    _require(tenant, Perm.RECOMMENDATIONS_ACTION, db.get(KpiDefinition, event.kpi_id).category)
    if recommendation.status not in ("open", "proposed"):
        raise ConflictError(
            f"This recommendation has already been dealt with ({recommendation.status}).",
            code="not_open",
        )
    recommendation.status = "dismissed"
    recommendation.basis = {**(recommendation.basis or {}), "dismissed_reason": body.reason}
    record_audit(
        db, AuditAction.RECOMMENDATION_DISMISSED, actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id, target_type="recommendation",
        target_id=recommendation.id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"reason": body.reason},
    )  # fmt: skip
    db.commit()


def approve(
    db: Session, tenant, action_id: uuid.UUID, note: str | None, meta: RequestMeta
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.RECOMMENDATIONS_ACTION, action.category)
    if action.status != "pending":
        raise ConflictError(
            "Only an action waiting for approval can be approved.", code="not_pending"
        )
    action.status = "accepted"
    action.approved_by_user_id = tenant.user.id
    if action.owner_user_id is None:
        action.owner_user_id = tenant.user.id
    _record(db, tenant, action, "approved", from_status="pending", to_status="accepted", note=note)
    _set_recommendation(db, action, "accepted")
    _audit(db, tenant, meta, AuditAction.ACTION_APPROVED, action.id)
    db.commit()
    return get_action(db, action.id)


def reject(
    db: Session, tenant, action_id: uuid.UUID, reason: str | None, meta: RequestMeta
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.RECOMMENDATIONS_ACTION, action.category)
    if action.status != "pending":
        raise ConflictError(
            "Only an action waiting for approval can be turned down.", code="not_pending"
        )
    action.status = "cancelled"
    _record(
        db, tenant, action, "rejected", from_status="pending", to_status="cancelled", note=reason
    )
    _set_recommendation(db, action, "open")  # the recommendation can be taken up again
    _audit(db, tenant, meta, AuditAction.ACTION_REJECTED, action.id)
    db.commit()
    return get_action(db, action.id)


def _set_recommendation(db: Session, action: BusinessAction, status: str) -> None:
    intervention = db.get(BusinessIntervention, action.intervention_id)
    if intervention.recommendation_id is None:
        return
    recommendation = db.get(Recommendation, intervention.recommendation_id)
    if recommendation is not None:
        recommendation.status = status


# --- working on it -------------------------------------------------------------------------------


def update(
    db: Session, tenant, action_id: uuid.UUID, body: ActionPatch, meta: RequestMeta
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.ACTIONS_MANAGE, action.category)
    if action.status in rules.FINAL:
        raise _closed()
    sent = body.model_fields_set
    if not sent:
        return get_action(db, action.id)

    if "title" in sent and body.title is not None and body.title != action.title:
        _record(
            db,
            tenant,
            action,
            "modification",
            note=f'Renamed to "{body.title}".',
            details={"was": action.title},
        )
        action.title = body.title
    if (
        "description" in sent
        and body.description is not None
        and body.description != action.description
    ):
        action.description = body.description
        _record(db, tenant, action, "modification", note="Changed the description.")
    if "steps" in sent and body.steps is not None:
        new_steps = rules.clean_steps([s.model_dump() for s in body.steps])
        if new_steps != action.steps:
            before, after = rules.progress(action.steps), rules.progress(new_steps)
            action.steps = new_steps
            _record(
                db, tenant, action, "steps",
                note=f"{after.done} of {after.total} steps done.",
                details={"done": after.done, "total": after.total, "was_done": before.done},
            )  # fmt: skip
    if "owner_user_id" in sent and body.owner_user_id != action.owner_user_id:
        who = _member(db, body.owner_user_id) if body.owner_user_id else None
        names = _people(db, {action.owner_user_id, who})
        was = names.get(action.owner_user_id)
        now_name = names.get(who)
        action.owner_user_id = who
        _record(
            db, tenant, action, "assignment",
            note=f"Now with {now_name.name}." if now_name else "No longer assigned to anyone.",
            details={"from": None if was is None else was.name, "to": None if now_name is None else now_name.name},
        )  # fmt: skip
    new_start = body.start_date if "start_date" in sent else action.start_date
    new_target = body.target_date if "target_date" in sent else action.target_date
    if not rules.dates_ok(new_start, new_target):
        raise AppError(
            "The target date is before the start date.", code="dates_out_of_order", status_code=422
        )
    if new_start != action.start_date or new_target != action.target_date:
        _record(
            db, tenant, action, "dates",
            note=f"Start {_words(new_start)}, due {_words(new_target)}.",
            details={"start_was": _words(action.start_date), "target_was": _words(action.target_date)},
        )  # fmt: skip
        action.start_date, action.target_date = new_start, new_target
    _sync_overdue(db, tenant, action, today_uk())
    _audit(db, tenant, meta, AuditAction.ACTION_UPDATED, action.id, changed=sorted(sent))
    db.commit()
    return get_action(db, action.id)


def set_status(
    db: Session, tenant, action_id: uuid.UUID, body: StatusIn, meta: RequestMeta
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.ACTIONS_MANAGE, action.category)
    current = action.status
    if current in rules.FINAL:
        raise _closed()
    if body.status == current:
        raise ConflictError(f"It is already {rules.LABELS[current].lower()}.", code="no_change")
    if not rules.can_move(current, body.status):
        options = ", ".join(rules.LABELS[s].lower() for s in rules.next_statuses(current))
        raise ConflictError(
            f"An action that is {rules.LABELS[current].lower()} can be moved to: {options}.",
            code="bad_transition",
            details={"allowed": rules.next_statuses(current)},
        )
    action.status = body.status
    if body.status == "completed":
        action.completed_at = utcnow()
    _record(
        db, tenant, action, "status", from_status=current, to_status=body.status, note=body.note
    )
    _audit(
        db, tenant, meta, AuditAction.ACTION_STATUS_CHANGED, action.id,
        from_status=current, to_status=body.status,
    )  # fmt: skip
    db.commit()
    return get_action(db, action.id)


def add_note(db: Session, tenant, action_id: uuid.UUID, note: str) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.ACTIONS_MANAGE, action.category)
    _record(db, tenant, action, "note", note=note)
    db.commit()
    return get_action(db, action.id)


# --- overdue -------------------------------------------------------------------------------------


def _sync_overdue(db: Session, tenant, action: BusinessAction, today: date) -> bool:
    """Mark it overdue if it has run past its date; take the mark off if the date has moved on."""
    if rules.is_overdue(action.status, action.target_date, today):
        previous = action.status
        action.status = "overdue"
        _record(
            db, tenant, action, "overdue", from_status=previous, to_status="overdue", system=True,
            note=f"Passed its target date of {_words(action.target_date)}.",
            details={"was": previous},
        )  # fmt: skip
        return True
    if rules.is_back_on_time(action.status, action.target_date, today):
        action.status = "in_progress"
        _record(
            db, tenant, action, "status", from_status="overdue", to_status="in_progress",
            system=True, note="The target date has been moved on, so it is no longer overdue.",
        )  # fmt: skip
        return True
    return False


def refresh_overdue(db: Session, tenant, today: date | None = None) -> int:
    """Mark every action that has run past its target date as overdue (and clear the mark where the
    date has moved on). Returns how many changed. Run whenever actions are looked at, and after the
    figures are worked out."""
    today = today or today_uk()
    changed = 0
    for action in db.scalars(
        select(BusinessAction).where(BusinessAction.status.in_(sorted(rules.OPEN)))
    ):
        changed += _sync_overdue(db, tenant, action, today)
    if changed:
        db.commit()
    return changed


# --- evidence ------------------------------------------------------------------------------------


def _evidence_out(e: ActionEvidence, people: dict) -> EvidenceOut:
    return EvidenceOut(
        id=e.id, kind=e.kind, title=e.title, note=e.note, url=e.url, filename=e.original_filename,
        content_type=e.content_type, size_bytes=e.size_bytes, user=people.get(e.user_id),
        created_at=e.created_at,
    )  # fmt: skip


def add_evidence(
    db: Session, tenant, action_id: uuid.UUID, body: EvidenceIn, meta: RequestMeta
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.ACTIONS_MANAGE, action.category)
    if body.kind == "link" and not body.url:
        raise AppError("Give the address of the page.", code="link_needs_url", status_code=422)
    if body.kind == "note" and not body.note:
        raise AppError("Write the note.", code="note_needs_text", status_code=422)
    db.add(
        ActionEvidence(
            organization_id=tenant.organization_id,
            action_id=action.id,
            user_id=tenant.user.id,
            kind=body.kind,
            title=body.title,
            note=body.note,
            url=body.url,
            created_at=utcnow(),
        )  # fmt: skip
    )
    _record(db, tenant, action, "evidence", note=f"Added {body.kind}: {body.title}")
    _audit(db, tenant, meta, AuditAction.ACTION_EVIDENCE_ADDED, action.id, kind=body.kind)
    db.commit()
    return get_action(db, action.id)


def add_file(
    db: Session,
    storage: FileStorage,
    tenant,
    action_id: uuid.UUID,
    *,
    stream: BinaryIO,
    filename: str,
    title: str | None,
    meta: RequestMeta,
) -> ActionOut:
    action = _get(db, action_id)
    _require(tenant, Perm.ACTIONS_MANAGE, action.category)
    name = PurePath(filename or "file").name[:255]
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise AppError(
            "That kind of file can't be attached. Use a picture (png, jpg), pdf, txt, csv, xlsx or docx.",
            code="file_type_not_allowed",
            status_code=422,
        )
    evidence_id = uuid.uuid4()
    key = evidence_key(tenant.organization_id, evidence_id, suffix)
    try:
        stored = storage.save(key, stream, max_bytes=MAX_EVIDENCE_BYTES)
    except FileTooLargeError as exc:
        raise AppError(
            "That file is larger than 5 MB.", code="file_too_large", status_code=413
        ) from exc
    try:
        db.add(
            ActionEvidence(
                id=evidence_id,
                organization_id=tenant.organization_id,
                action_id=action.id,
                user_id=tenant.user.id,
                kind="file",
                title=(title or name)[:200],
                storage_key=key,
                original_filename=name,
                content_type=CONTENT_TYPES[suffix],
                size_bytes=stored.size_bytes,
                created_at=utcnow(),
            )  # fmt: skip
        )
        _record(db, tenant, action, "evidence", note=f"Attached file: {name}")
        _audit(db, tenant, meta, AuditAction.ACTION_EVIDENCE_ADDED, action.id, kind="file")
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)  # never leave a file nothing points to
        raise
    return get_action(db, action.id)


def evidence_file(
    db: Session, storage: FileStorage, action_id: uuid.UUID, evidence_id: uuid.UUID
) -> tuple[ActionEvidence, BinaryIO]:
    item = db.scalars(
        select(ActionEvidence).where(
            ActionEvidence.id == evidence_id,
            ActionEvidence.action_id == action_id,
            ActionEvidence.kind == "file",
        )
    ).first()
    if item is None or not storage.exists(item.storage_key):
        raise NotFoundError("That file was not found", code="evidence_not_found")
    return item, storage.open(item.storage_key)


# --- reading -------------------------------------------------------------------------------------


def _decision(db: Session, action: BusinessAction, people: dict) -> WhatWasDecidedOut:
    i = db.get(BusinessIntervention, action.intervention_id)
    kpi = db.get(KpiDefinition, i.kpi_id)

    def money(v):
        return None if v is None else str(Decimal(v).quantize(Decimal("0.01")))

    return WhatWasDecidedOut(
        title=i.title, original_title=i.original_title, modified=bool(i.basis.get("modified")),
        description=i.description, target=i.target_label, library_code=i.library_code,
        kpi_code=kpi.code, kpi_name=kpi.name, unit=kpi.unit, baseline_period=i.baseline_period,
        baseline_value=money(i.baseline_value), expected_impact_value=money(i.expected_impact_value),
        expected_impact_unit=i.expected_impact_unit, recommendation_score=i.recommendation_score,
        accepted_by=people.get(i.accepted_by_user_id), accepted_at=i.accepted_at, event_id=i.event_id,
    )  # fmt: skip


def get_action(db: Session, action_id: uuid.UUID, today: date | None = None) -> ActionOut:
    action = _get(db, action_id)
    today = today or today_uk()
    updates = db.scalars(
        select(ActionUpdate)
        .where(ActionUpdate.action_id == action.id)
        .order_by(ActionUpdate.created_at, ActionUpdate.id)
    ).all()
    evidence = db.scalars(
        select(ActionEvidence)
        .where(ActionEvidence.action_id == action.id)
        .order_by(ActionEvidence.created_at, ActionEvidence.id)
    ).all()
    intervention = db.get(BusinessIntervention, action.intervention_id)
    people = _people(
        db,
        {
            action.owner_user_id, action.created_by_user_id, action.approved_by_user_id,
            intervention.accepted_by_user_id, *(u.user_id for u in updates), *(e.user_id for e in evidence),
        },
    )  # fmt: skip
    progress = rules.progress(action.steps)
    return ActionOut(
        id=action.id, title=action.title, description=action.description, category=action.category,
        status=action.status, status_label=rules.LABELS[action.status],
        next_statuses=rules.next_statuses(action.status), owner=people.get(action.owner_user_id),
        created_by=people.get(action.created_by_user_id),
        approved_by=people.get(action.approved_by_user_id), start_date=action.start_date,
        target_date=action.target_date, completed_at=action.completed_at,
        days_late=rules.days_late(action.target_date, today) if action.status == "overdue" else 0,
        due_soon=rules.due_soon(action.status, action.target_date, today),
        steps=[StepOut(**s) for s in action.steps],
        progress=ProgressOut(done=progress.done, total=progress.total, percent=progress.percent),
        decision=_decision(db, action, people),
        updates=[
            UpdateOut(
                id=u.id, kind=u.kind, from_status=u.from_status, to_status=u.to_status, note=u.note,
                details=u.details, user=people.get(u.user_id), created_at=u.created_at,
            )
            for u in updates
        ],
        evidence=[_evidence_out(e, people) for e in evidence],
        last_activity_at=action.last_activity_at,
    )  # fmt: skip


def list_actions(
    db: Session,
    tenant,
    *,
    status: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    mine: bool = False,
    open_only: bool = False,
    today: date | None = None,
) -> list[ActionSummaryOut]:
    """Overdue first, then by target date, then newest. Looking at the list brings overdue marks
    up to date first."""
    today = today or today_uk()
    refresh_overdue(db, tenant, today)
    query = select(BusinessAction, BusinessIntervention.kpi_id).join(
        BusinessIntervention,
        (BusinessIntervention.organization_id == BusinessAction.organization_id)
        & (BusinessIntervention.id == BusinessAction.intervention_id),
    )
    if status is not None:
        query = query.where(BusinessAction.status == status)
    if open_only:
        query = query.where(BusinessAction.status.in_(sorted(rules.OPEN | {"pending"})))
    if mine:
        owner_user_id = tenant.user.id
    if owner_user_id is not None:
        query = query.where(BusinessAction.owner_user_id == owner_user_id)
    query = query.order_by(
        case((BusinessAction.status == "overdue", 0), else_=1),
        BusinessAction.target_date.asc().nulls_last(),
        BusinessAction.created_at.desc(),
    )
    rows = db.execute(query).all()
    people = _people(db, {a.owner_user_id for a, _ in rows})
    kpis = {k.id: k.name for k in db.scalars(select(KpiDefinition))}
    out = []
    for action, kpi_id in rows:
        progress = rules.progress(action.steps)
        out.append(
            ActionSummaryOut(
                id=action.id,
                title=action.title,
                category=action.category,
                kpi_name=kpis[kpi_id],
                status=action.status,
                status_label=rules.LABELS[action.status],
                owner=people.get(action.owner_user_id),
                start_date=action.start_date,
                target_date=action.target_date,
                days_late=rules.days_late(action.target_date, today)
                if action.status == "overdue"
                else 0,
                due_soon=rules.due_soon(action.status, action.target_date, today),
                progress=ProgressOut(
                    done=progress.done, total=progress.total, percent=progress.percent
                ),
                last_activity_at=action.last_activity_at,
            )  # fmt: skip
        )
    return out


def counts(db: Session, tenant, today: date | None = None) -> ActionCountsOut:
    today = today or today_uk()
    refresh_overdue(db, tenant, today)
    by_status = dict(
        db.execute(
            select(BusinessAction.status, func.count()).group_by(BusinessAction.status)
        ).all()
    )
    actions = db.scalars(
        select(BusinessAction).where(BusinessAction.status.in_(sorted(rules.OPEN)))
    ).all()
    return ActionCountsOut(
        pending=by_status.get("pending", 0), accepted=by_status.get("accepted", 0),
        in_progress=by_status.get("in_progress", 0),
        partially_completed=by_status.get("partially_completed", 0),
        completed=by_status.get("completed", 0), cancelled=by_status.get("cancelled", 0),
        overdue=by_status.get("overdue", 0), open=len(actions),
        due_soon=sum(rules.due_soon(a.status, a.target_date, today) for a in actions),
        mine_open=sum(1 for a in actions if a.owner_user_id == tenant.user.id),
    )  # fmt: skip
