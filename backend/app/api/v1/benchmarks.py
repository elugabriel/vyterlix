from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DB, Tenant, VerifiedUser, require_permission
from app.core.errors import NotFoundError
from app.core.permissions import Perm
from app.models.business import BusinessProfile
from app.schemas.benchmarks import BenchmarkOut, MatchedBenchmarkOut, Region, SizeBand
from app.services.benchmarks import best_matches, search

router = APIRouter(tags=["benchmarks"])

Reader = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]


@router.get("/benchmarks", response_model=list[BenchmarkOut])
def list_benchmarks(
    user: VerifiedUser,
    db: DB,
    industry_code: str | None = None,
    kpi_code: str | None = None,
    region: Region | None = None,
    size_band: SizeBand | None = None,
    sic_code: Annotated[str | None, Query(pattern=r"^[0-9]{5}$")] = None,
    period_year: int | None = None,
):
    """Shared UK sector figures (read-only). Loaded by staff; every figure cites a source."""
    return search(
        db,
        industry_code=industry_code,
        kpi_code=kpi_code,
        region=region,
        size_band=size_band,
        sic_code=sic_code,
        period_year=period_year,
    )


@router.get("/organizations/{organization_id}/benchmarks", response_model=list[MatchedBenchmarkOut])
def my_benchmarks(tenant: Reader, db: DB):
    """The closest benchmarks for this business, one per KPI, saying how specific each is."""
    profile = db.scalar(select(BusinessProfile))
    if profile is None:
        raise NotFoundError(
            "Set up the business profile first, so we know which sector to compare with",
            code="profile_not_set_up",
        )
    return best_matches(db, profile)
