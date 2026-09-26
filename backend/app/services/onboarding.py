"""Onboarding progress: how far a business has got with setting up Vyterlix.

Each section's status is worked out from the data that section creates, so progress can
never disagree with what's actually set up. Only "skipped" is stored. Required sections
(decision 2026-09-26) must be done before the dashboard; everything else is optional.
The session must be scoped to the organisation.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError
from app.models.business import BusinessGoal, BusinessListItem, BusinessProfile, BusinessSeason
from app.models.identity import OrganizationInvitation, OrganizationUser
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

Status = Literal["done", "to_do", "skipped"]


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def _has_list(kind: str) -> Callable[[Session, BusinessProfile | None], bool]:
    def check(db: Session, profile: BusinessProfile | None) -> bool:
        return (
            _count(
                db,
                BusinessListItem,
                BusinessListItem.kind == kind,
                BusinessListItem.is_active.is_(True),
            )
            > 0
        )

    return check


def _team(db: Session, profile: BusinessProfile | None) -> bool:
    others = _count(db, OrganizationUser, OrganizationUser.status == "active") > 1
    pending = _count(
        db,
        OrganizationInvitation,
        OrganizationInvitation.accepted_at.is_(None),
        OrganizationInvitation.revoked_at.is_(None),
        OrganizationInvitation.expires_at > func.now(),
    )
    return others or pending > 0


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    required: bool
    hint: str
    is_done: Callable[[Session, BusinessProfile | None], bool]


# The order is the order the onboarding wizard walks through.
SECTIONS: tuple[Section, ...] = (
    Section(
        "business_details",
        "Business details",
        True,
        "Your industry and when your financial year starts",
        lambda db, p: p is not None,
    ),
    Section(
        "location",
        "Where you're based",
        False,
        "Your UK region and town or postcode, for local comparisons",
        lambda db, p: (
            p is not None and p.region is not None and (p.town_city or p.postcode) is not None
        ),
    ),
    Section(
        "goals",
        "Your goals",
        False,
        "What you want to achieve, e.g. grow revenue to £250,000",
        lambda db, p: _count(db, BusinessGoal, BusinessGoal.status == "active") > 0,
    ),
    Section(
        "offerings", "What you sell", False, "Your main products or services", _has_list("offering")
    ),
    Section(
        "sales_channels",
        "Where you sell",
        False,
        "E.g. your shop, website, Amazon or Deliveroo",
        _has_list("sales_channel"),
    ),
    Section(
        "customer_types",
        "Who your customers are",
        False,
        "E.g. consumers, small businesses, trade customers",
        _has_list("customer_type"),
    ),
    Section(
        "cost_categories",
        "Your main costs",
        False,
        "E.g. stock, staff wages, rent, business rates",
        _has_list("cost_category"),
    ),
    Section(
        "seasons",
        "Busy and quiet times",
        False,
        "E.g. a Christmas rush or a quiet August",
        lambda db, p: _count(db, BusinessSeason, BusinessSeason.status == "active") > 0,
    ),
    Section("team", "Invite your team", False, "Add the people who help run the business", _team),
)
SECTION_KEYS = tuple(s.key for s in SECTIONS)
OPTIONAL_KEYS = tuple(s.key for s in SECTIONS if not s.required)


def _profile(db: Session) -> BusinessProfile | None:
    return db.scalar(select(BusinessProfile))


def progress(db: Session) -> dict:
    profile = _profile(db)
    skipped = set(profile.onboarding_skipped) if profile else set()
    sections = []
    for s in SECTIONS:
        status: Status = (
            "done" if s.is_done(db, profile) else "skipped" if s.key in skipped else "to_do"
        )
        sections.append(
            {
                "key": s.key,
                "title": s.title,
                "required": s.required,
                "status": status,
                "hint": s.hint,
            }
        )

    required = [x for x in sections if x["required"]]
    optional = [x for x in sections if not x["required"]]
    to_do = [x for x in sections if x["status"] == "to_do"]
    next_section = next((x for x in to_do if x["required"]), None) or next(iter(to_do), None)
    return {
        "ready_for_dashboard": all(x["status"] == "done" for x in required),
        "completed_at": profile.onboarding_completed_at if profile else None,
        "done": sum(x["status"] == "done" for x in sections),
        "total": len(sections),
        "required_done": sum(x["status"] == "done" for x in required),
        "required_total": len(required),
        "optional_done": sum(x["status"] == "done" for x in optional),
        "optional_skipped": sum(x["status"] == "skipped" for x in optional),
        "optional_total": len(optional),
        "next_section": next_section["key"] if next_section else None,
        "sections": sections,
    }


def _require_profile(db: Session) -> BusinessProfile:
    profile = _profile(db)
    if profile is None:
        raise ConflictError("Add your business details first", code="business_details_required")
    return profile


def set_skipped(db: Session, section: str, skip: bool) -> dict:
    if section not in OPTIONAL_KEYS:
        raise AppError(
            "Only optional sections can be skipped", code="section_required", status_code=422
        )
    profile = _require_profile(db)
    skipped = set(profile.onboarding_skipped)
    skipped = skipped | {section} if skip else skipped - {section}
    profile.onboarding_skipped = sorted(skipped)
    db.commit()
    return progress(db)


def complete(db: Session, tenant, meta: RequestMeta) -> dict:
    """Mark setup finished. Needs every required section; optional ones can come later."""
    state = progress(db)
    if not state["ready_for_dashboard"]:
        missing = [x["title"] for x in state["sections"] if x["required"] and x["status"] != "done"]
        raise AppError(
            f"Finish these first: {', '.join(missing)}",
            code="onboarding_incomplete",
            status_code=409,
            details={"missing": missing},
        )
    profile = _require_profile(db)
    if profile.onboarding_completed_at is None:
        profile.onboarding_completed_at = func.now()
        record_audit(
            db,
            AuditAction.ONBOARDING_COMPLETED,
            actor_user_id=tenant.user.id,
            organization_id=tenant.organization_id,
            target_type="business_profile",
            target_id=profile.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={
                "optional_done": state["optional_done"],
                "optional_total": state["optional_total"],
            },
        )
        db.commit()
    return progress(db)
