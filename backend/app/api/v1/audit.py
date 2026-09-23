import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.audit import AuditLogPage
from app.services.audit import AuditAction, list_audit_log

router = APIRouter(prefix="/organizations/{organization_id}/audit-log", tags=["audit"])


@router.get("", response_model=AuditLogPage)
def read_audit_log(
    tenant: Annotated[Tenant, Depends(require_permission(Perm.AUDIT_VIEW))],
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: uuid.UUID | None = None,
    action: AuditAction | None = None,
):
    """Who did what in this business, newest first. Owners only (audit.view)."""
    return list_audit_log(db, tenant.organization_id, limit=limit, before=before, action=action)
