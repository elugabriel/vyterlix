from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.memory import ConstraintsIn, ConstraintsOut, MemoryOut, RebuildOut
from app.services import memory as service

router = APIRouter(prefix="/organizations/{organization_id}/memory", tags=["memory"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Worker = Annotated[Tenant, Depends(require_permission(Perm.ACTIONS_MANAGE))]
Owner = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=MemoryOut)
def memory(tenant: Viewer, db: DB):
    """Everything Vyterlix has learned about this business: what is normal, patterns in customers,
    goals and seasons, the owner's limits, what has been tried and how it went, and what was
    remembered when recent recommendations were made."""
    return service.read(db)


@router.post("/rebuild", response_model=RebuildOut)
def rebuild(tenant: Worker, db: DB):
    """Work out again what is normal and what the patterns are, from the records as they are now."""
    return service.rebuild(db, tenant)


@router.get("/constraints", response_model=ConstraintsOut)
def constraints(tenant: Viewer, db: DB):
    return service.constraints_out(db)


@router.put("/constraints", response_model=ConstraintsOut)
def set_constraints(body: ConstraintsIn, tenant: Owner, db: DB):
    """Set the limits on what can be suggested: cost, effort, actions never suggested, speed."""
    return service.set_constraints(db, tenant, body)
