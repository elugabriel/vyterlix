import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.actions import AcceptIn, DismissIn
from app.schemas.alert_card import AlertCardOut
from app.schemas.alerts import (
    AlertCategory,
    AlertCountsOut,
    AlertDetailOut,
    AlertNoteIn,
    AlertOut,
    AlertStatus,
    EvaluateOut,
    InboxOut,
    PostponeIn,
    RuleIn,
    RuleOut,
    Severity,
)
from app.services import alert_card, notifications
from app.services import alerts as service
from app.services.email import EmailSender, get_email_sender

router = APIRouter(prefix="/organizations/{organization_id}/alerts", tags=["alerts"])
inbox_router = APIRouter(
    prefix="/organizations/{organization_id}/notifications", tags=["notifications"]
)

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Worker = Annotated[Tenant, Depends(require_permission(Perm.ACTIONS_MANAGE))]
Decider = Annotated[Tenant, Depends(require_permission(Perm.RECOMMENDATIONS_ACTION))]
Owner = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=list[AlertOut])
def alerts(
    tenant: Viewer,
    db: DB,
    status: AlertStatus | None = None,
    severity: Severity | None = None,
    category: AlertCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """The alert history: open ones first, the most serious first, then the most recent."""
    return service.list_alerts(db, status=status, severity=severity, category=category, limit=limit)


@router.get("/summary", response_model=AlertCountsOut)
def summary(tenant: Viewer, db: DB):
    return service.counts(db)


@router.get("/rules", response_model=list[RuleOut])
def rules(tenant: Viewer, db: DB):
    """Every kind of alert, with this business's settings for it (or the defaults)."""
    return service.rules_out(db)


@router.put("/rules/{code}", response_model=RuleOut)
def set_rule(code: str, body: RuleIn, tenant: Owner, db: DB, meta: Meta):
    """Switch a kind of alert on or off, change how serious it is, or change its threshold."""
    return service.set_rule(db, tenant, code, body, meta)


@router.post("/evaluate", response_model=EvaluateOut)
def evaluate(tenant: Worker, db: DB, sender: Annotated[EmailSender, Depends(get_email_sender)]):
    """Look for things that need attention now, instead of waiting for the next round."""
    return service.evaluate(db, tenant, sender=sender)


@router.get("/{alert_id}", response_model=AlertDetailOut)
def one(alert_id: uuid.UUID, tenant: Viewer, db: DB):
    return service.get_alert(db, alert_id)


@router.get("/{alert_id}/card", response_model=AlertCardOut)
def card(alert_id: uuid.UUID, tenant: Viewer, db: DB):
    """The alert with everything needed to decide: what happened in plain words, why it matters,
    how urgent, a chart, and the suggested next step (worked out the first time someone who may
    decide opens it)."""
    return alert_card.card(db, tenant, alert_id)


@router.post("/{alert_id}/accept", response_model=AlertCardOut)
def accept(alert_id: uuid.UUID, tenant: Decider, db: DB, meta: Meta, body: AcceptIn | None = None):
    """Accept the suggestion: it becomes an action (with an owner and dates, changed first if you
    want) and the alert is marked as being dealt with. Send nothing to take it as suggested."""
    return alert_card.accept(db, tenant, alert_id, body or AcceptIn(), meta)


@router.post("/{alert_id}/dismiss", response_model=AlertCardOut)
def dismiss(
    alert_id: uuid.UUID, tenant: Decider, db: DB, meta: Meta, body: DismissIn | None = None
):
    """Decide not to act. The suggestion stays on record with the reason and the alert is closed."""
    return alert_card.dismiss(db, tenant, alert_id, body or DismissIn(), meta)


@router.post("/{alert_id}/postpone", response_model=AlertCardOut)
def postpone(alert_id: uuid.UUID, body: PostponeIn, tenant: Worker, db: DB, meta: Meta):
    """Put an open alert off for some days, or until a day. It stays open but leaves what needs
    attention, and comes back by itself on that day."""
    return alert_card.postpone(db, tenant, alert_id, body, meta)


@router.post("/{alert_id}/bring-back", response_model=AlertCardOut)
def bring_back(alert_id: uuid.UUID, tenant: Worker, db: DB, meta: Meta):
    """Bring a postponed alert back now."""
    return alert_card.bring_back(db, tenant, alert_id, meta)


@router.post("/{alert_id}/acknowledge", response_model=AlertDetailOut)
def acknowledge(
    alert_id: uuid.UUID, tenant: Worker, db: DB, meta: Meta, body: AlertNoteIn | None = None
):
    """Say you have seen it and are on it."""
    return service.acknowledge(db, tenant, alert_id, None if body is None else body.note, meta)


@router.post("/{alert_id}/resolve", response_model=AlertDetailOut)
def resolve(
    alert_id: uuid.UUID, tenant: Worker, db: DB, meta: Meta, body: AlertNoteIn | None = None
):
    """Close it: it has been dealt with, or it does not matter."""
    return service.resolve(db, tenant, alert_id, None if body is None else body.note, meta)


@inbox_router.get("", response_model=InboxOut)
def inbox(
    tenant: Viewer,
    db: DB,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Your own notifications, newest first, with how many you have not read."""
    return notifications.inbox(db, tenant, unread_only=unread_only, limit=limit)


@inbox_router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def read_all(tenant: Viewer, db: DB):
    notifications.mark_all_read(db, tenant)


@inbox_router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read(notification_id: uuid.UUID, tenant: Viewer, db: DB):
    notifications.mark_read(db, tenant, notification_id)
