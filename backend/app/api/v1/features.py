from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.admin import FeaturesOut
from app.services import flags

router = APIRouter(prefix="/organizations/{organization_id}/features", tags=["features"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("", response_model=FeaturesOut)
def features(tenant: Viewer, db: DB):
    """Which switchable features are on for this business (for the website and the mobile apps)."""
    return FeaturesOut(flags=flags.flags_for(db, tenant.organization_id))
