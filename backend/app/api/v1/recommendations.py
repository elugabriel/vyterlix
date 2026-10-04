import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from app.api.deps import DB, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.recommendations import (
    InterventionOut,
    RecommendationOut,
    RecommendationSummaryOut,
)
from app.services.recommendations import generate, interventions, list_recommendations, read

router = APIRouter(prefix="/organizations/{organization_id}", tags=["recommendations"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Decider = Annotated[Tenant, Depends(require_permission(Perm.RECOMMENDATIONS_ACTION))]


@router.get("/interventions", response_model=list[InterventionOut])
def library(tenant: Viewer, db: DB):
    """The actions Vyterlix knows how to suggest, with the steps, effort, cost and how long each
    takes to show. The share it usually wins back is a starting estimate."""
    return interventions(db)


@router.get("/recommendations", response_model=list[RecommendationSummaryOut])
def recommendations(
    tenant: Viewer,
    db: DB,
    status: Literal["open", "no_action_needed", "insufficient_evidence", "dismissed"] | None = None,
):
    """Every recommendation made, newest first."""
    return list_recommendations(db, status)


@router.get("/changes/{event_id}/recommendation", response_model=RecommendationOut)
def get_recommendation(event_id: uuid.UUID, tenant: Viewer, db: DB):
    """What to do about this change, the options considered and why one was chosen (404 until it
    has been worked out)."""
    return read(db, event_id)


@router.post("/changes/{event_id}/recommendation", response_model=RecommendationOut)
def recommend(event_id: uuid.UUID, tenant: Decider, db: DB):
    """Work out what to do about this change (explaining it first if that has not been done).
    Safe to run again: the recommendation and its options are replaced."""
    return generate(db, tenant, event_id)
