import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.core.uk import today_uk
from app.schemas.seasons import SeasonCreate, SeasonOut, SeasonPatch
from app.services.seasons import create_season, list_seasons, seasons_on, update_season

router = APIRouter(prefix="/organizations/{organization_id}/seasons", tags=["seasons"])

Reader = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
OrgManager = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


@router.get("", response_model=list[SeasonOut])
def list_(
    tenant: Reader,
    db: DB,
    status: Literal["active", "suggested", "dismissed"] | None = None,
):
    """Suggestions first, then calendar order. Dismissed seasons only when asked for."""
    return list_seasons(db, status)


@router.get("/on", response_model=list[SeasonOut])
def on_date(tenant: Reader, db: DB, date: date | None = None):
    """Active seasons that include a date (default: today in the UK)."""
    return seasons_on(db, date or today_uk())


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SeasonOut)
def create(body: SeasonCreate, tenant: OrgManager, db: DB, meta: Meta):
    return create_season(db, tenant, body, meta)


@router.patch("/{season_id}", response_model=SeasonOut)
def update(season_id: uuid.UUID, body: SeasonPatch, tenant: OrgManager, db: DB, meta: Meta):
    """Edit a season, confirm a suggestion (`status: active`) or dismiss one."""
    return update_season(db, tenant, season_id, body, meta)
