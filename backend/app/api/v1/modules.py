from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentTenant, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.kpi import ModuleChoiceIn, ModuleStateOut
from app.services.module_choices import list_states, set_choice

router = APIRouter(prefix="/organizations/{organization_id}/modules", tags=["modules"])

OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=list[ModuleStateOut])
def modules(tenant: CurrentTenant, db: DB):
    """The twelve parts of Vyterlix and which of them this business sees."""
    return list_states(db)


@router.put("/{code}", response_model=ModuleStateOut)
def choose(code: str, body: ModuleChoiceIn, tenant: OrgManager, db: DB, meta: Meta):
    """The owner shows or hides one part."""
    return set_choice(db, tenant, code, body.enabled, meta)
