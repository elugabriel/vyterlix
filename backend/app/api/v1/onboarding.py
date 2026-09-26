from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentTenant, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.onboarding import OnboardingOut, SkipRequest
from app.services.onboarding import complete, progress, set_skipped

router = APIRouter(prefix="/organizations/{organization_id}/onboarding", tags=["onboarding"])

OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=OnboardingOut)
def read_progress(tenant: CurrentTenant, db: DB):
    return progress(db)


@router.post("/skip", response_model=OnboardingOut)
def skip_section(body: SkipRequest, tenant: OrgManager, db: DB):
    """Skip an optional section for now (or `skip: false` to bring it back)."""
    return set_skipped(db, body.section, body.skip)


@router.post("/complete", response_model=OnboardingOut)
def finish(tenant: OrgManager, db: DB, meta: Meta):
    """Finish setup. Needs the required sections; optional ones can be done later."""
    return complete(db, tenant, meta)
