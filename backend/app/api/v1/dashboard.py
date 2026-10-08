from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.dashboard import DashboardOut
from app.services import dashboard as service

router = APIRouter(prefix="/organizations/{organization_id}/dashboard", tags=["dashboard"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("", response_model=DashboardOut)
def dashboard(tenant: Viewer, db: DB):
    """The front screen: what needs attention today (most serious first, and only what this person
    can see), then how the business is doing."""
    return service.read(db, tenant)
