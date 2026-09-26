import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.goals import GoalCreate, GoalOut, GoalPatch, GoalStatus
from app.services.goals import create_goal, get_goal, list_goals, update_goal

router = APIRouter(prefix="/organizations/{organization_id}/goals", tags=["goals"])

Reader = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
# Owners and Managers; the service also checks a Manager's remit for the goal's area.
GoalManager = Annotated[Tenant, Depends(require_permission(Perm.GOALS_MANAGE))]


@router.get("", response_model=list[GoalOut])
def list_(tenant: Reader, db: DB, status: GoalStatus | None = None):
    """All goals (or only one status), active first, then by priority and target date."""
    return list_goals(db, status)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GoalOut)
def create(body: GoalCreate, tenant: GoalManager, db: DB, meta: Meta):
    return create_goal(db, tenant, body, meta)


@router.get("/{goal_id}", response_model=GoalOut)
def read(goal_id: uuid.UUID, tenant: Reader, db: DB):
    return get_goal(db, goal_id)


@router.patch("/{goal_id}", response_model=GoalOut)
def update(goal_id: uuid.UUID, body: GoalPatch, tenant: GoalManager, db: DB, meta: Meta):
    """Change a goal; set `status` to "achieved" or "abandoned" to close it."""
    return update_goal(db, tenant, goal_id, body, meta)
