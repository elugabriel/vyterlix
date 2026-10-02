from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.data_quality import DataQualityOut, RefreshOut, StoredIssueOut
from app.services.data_quality import build_report, list_stored_issues, refresh_report

router = APIRouter(prefix="/organizations/{organization_id}/data-quality", tags=["data quality"])

Reader = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
DataManager = Annotated[Tenant, Depends(require_permission(Perm.DATA_MANAGE))]


@router.get("", response_model=DataQualityOut)
def report(tenant: Reader, db: DB, as_of: date | None = None):
    """How complete and trustworthy the business's data is: an overall score, the checks behind
    it, a month-by-month table, how each recent import went, and what to fix first.
    Worked out fresh from the data every time. `as_of` defaults to today in the UK."""
    return build_report(db, as_of)


@router.post("/refresh", response_model=RefreshOut)
def refresh(tenant: DataManager, db: DB, meta: Meta, as_of: date | None = None):
    """Same report, and the problems found are saved (new ones added, fixed ones marked
    resolved) so alerts and the health score can rely on them."""
    return refresh_report(db, tenant, meta, as_of)


@router.get("/issues", response_model=list[StoredIssueOut])
def stored_issues(
    tenant: Reader,
    db: DB,
    status: Literal["open", "resolved", "all"] = "open",
    severity: Literal["info", "warning", "critical"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """The problems saved by the last refreshes, newest first."""
    return list_stored_issues(db, status=status, severity=severity, limit=limit, offset=offset)
