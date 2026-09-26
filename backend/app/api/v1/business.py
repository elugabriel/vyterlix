from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentTenant, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.business import (
    BusinessProfileIn,
    BusinessProfileOut,
    BusinessProfilePatch,
    IndustryOut,
)
from app.services.business_profile import (
    get_profile,
    list_industries,
    patch_profile,
    put_profile,
)

industries_router = APIRouter(prefix="/industries", tags=["business profile"])
profile_router = APIRouter(
    prefix="/organizations/{organization_id}/profile", tags=["business profile"]
)

OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@industries_router.get("", response_model=list[IndustryOut])
def industries(db: DB):
    """The industry list for the profile form. Public reference data."""
    return list_industries(db)


@profile_router.get("", response_model=BusinessProfileOut)
def read_profile(tenant: CurrentTenant, db: DB):
    """Any member can read. 404 `profile_not_set_up` means onboarding hasn't been done."""
    return get_profile(db, tenant.organization)


@profile_router.put("", response_model=BusinessProfileOut)
def set_profile(body: BusinessProfileIn, tenant: OrgManager, db: DB, meta: Meta):
    """Create or replace the whole profile (owners)."""
    return put_profile(db, tenant.organization, tenant.user, body, meta)


@profile_router.patch("", response_model=BusinessProfileOut)
def update_profile(body: BusinessProfilePatch, tenant: OrgManager, db: DB, meta: Meta):
    """Change some fields (owners)."""
    return patch_profile(db, tenant.organization, tenant.user, body, meta)
