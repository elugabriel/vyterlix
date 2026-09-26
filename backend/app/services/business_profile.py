"""The business profile (one per organisation). Session must be scoped to the organisation."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.core.uk import today_uk
from app.models.business import BusinessProfile, Industry
from app.models.identity import Organization, User
from app.schemas.business import (
    BusinessProfileIn,
    BusinessProfileOut,
    BusinessProfilePatch,
    FinancialYearStart,
    IndustryOut,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

# Every column the API manages. `currency` and `country` are fixed to GBP/GB (UK-first).
_OPTIONAL = (
    "sic_code",
    "region",
    "town_city",
    "postcode",
    "business_size",
    "business_model",
    "team_size",
    "founded_year",
    "vat_registered",
    "vat_number",
)


def list_industries(db: Session) -> list[IndustryOut]:
    rows = db.scalars(
        select(Industry).where(Industry.is_active.is_(True)).order_by(Industry.sort_order)
    )
    return [IndustryOut(code=i.code, label=i.label) for i in rows]


def _industry(db: Session, code: str) -> Industry:
    industry = db.get(Industry, code)
    if industry is None or not industry.is_active:
        raise AppError("Choose an industry from the list", code="unknown_industry", status_code=422)
    return industry


def _to_columns(fields: dict[str, Any]) -> dict[str, Any]:
    """API shape -> table columns (financial_year_start becomes two columns)."""
    columns = dict(fields)
    start = columns.pop("financial_year_start", None)
    if start is not None:
        columns["fiscal_year_start_month"] = start["month"]
        columns["fiscal_year_start_day"] = start["day"]
    return columns


def _reconcile_vat(profile: BusinessProfile) -> None:
    # Giving a VAT number means VAT-registered; switching registration off clears the number.
    if profile.vat_number is not None and profile.vat_registered is None:
        profile.vat_registered = True
    if profile.vat_registered is False:
        profile.vat_number = None


def _out(db: Session, org: Organization, p: BusinessProfile) -> BusinessProfileOut:
    industry = db.get(Industry, p.industry_code)
    return BusinessProfileOut(
        organization_id=org.id,
        name=org.name,
        industry=IndustryOut(code=industry.code, label=industry.label),
        financial_year_start=FinancialYearStart(
            month=p.fiscal_year_start_month, day=p.fiscal_year_start_day
        ),
        currency=p.currency,
        country=p.country,
        sic_code=p.sic_code,
        region=p.region,
        town_city=p.town_city,
        postcode=p.postcode,
        business_size=p.business_size,
        business_model=p.business_model,
        team_size=p.team_size,
        founded_year=p.founded_year,
        years_operating=today_uk().year - p.founded_year if p.founded_year else None,
        vat_registered=p.vat_registered,
        vat_number=p.vat_number,
        onboarding_completed_at=p.onboarding_completed_at,
        updated_at=p.updated_at,
    )


def _current(db: Session) -> BusinessProfile | None:
    return db.scalar(select(BusinessProfile))  # tenant scoping limits this to one business


def _not_set_up() -> NotFoundError:
    return NotFoundError("This business hasn't set up its profile yet", code="profile_not_set_up")


def get_profile(db: Session, org: Organization) -> BusinessProfileOut:
    profile = _current(db)
    if profile is None:
        raise _not_set_up()
    return _out(db, org, profile)


def _save(
    db: Session,
    org: Organization,
    actor: User,
    profile: BusinessProfile,
    columns: dict[str, Any],
    created: bool,
    meta: RequestMeta,
) -> BusinessProfileOut:
    before = {name: getattr(profile, name) for name in columns}
    for name, value in columns.items():
        setattr(profile, name, value)
    _reconcile_vat(profile)
    changed = sorted(name for name in columns if created or getattr(profile, name) != before[name])
    if created or changed:
        record_audit(
            db,
            AuditAction.BUSINESS_PROFILE_CREATED
            if created
            else AuditAction.BUSINESS_PROFILE_UPDATED,
            actor_user_id=actor.id,
            organization_id=org.id,
            target_type="business_profile",
            target_id=profile.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"fields": changed},  # field names only
        )
        db.commit()
        db.refresh(profile)
    return _out(db, org, profile)


def put_profile(
    db: Session, org: Organization, actor: User, body: BusinessProfileIn, meta: RequestMeta
) -> BusinessProfileOut:
    """Create the profile, or replace it entirely (omitted optional fields are cleared)."""
    _industry(db, body.industry_code)
    fields = body.model_dump(mode="json", exclude={"currency", "country"})
    columns = _to_columns(fields)
    profile = _current(db)
    created = profile is None
    if created:
        profile = BusinessProfile(
            **{
                k: columns[k]
                for k in ("industry_code", "fiscal_year_start_month", "fiscal_year_start_day")
            }
        )
        db.add(profile)
        db.flush()
    return _save(db, org, actor, profile, columns, created, meta)


def patch_profile(
    db: Session, org: Organization, actor: User, body: BusinessProfilePatch, meta: RequestMeta
) -> BusinessProfileOut:
    """Change only the fields sent. The profile must already exist."""
    profile = _current(db)
    if profile is None:
        raise _not_set_up()
    fields = body.model_dump(mode="json", include=body.model_fields_set)
    if "industry_code" in fields:
        _industry(db, fields["industry_code"])
    return _save(db, org, actor, profile, _to_columns(fields), False, meta)
