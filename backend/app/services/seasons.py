"""Recurring busy and quiet periods. Session must be scoped to the organisation.

Later phases use `seasons_on()` so a normal seasonal dip isn't reported as a problem,
and seasonality detection (Phase 4+) adds seasons with source="detected",
status="suggested" for the owner to confirm or dismiss. Nothing detected is applied silently.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.business import BusinessSeason
from app.schemas.seasons import DayOfYear, SeasonCreate, SeasonOut, SeasonPatch
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _label(start_month: int, start_day: int, end_month: int, end_day: int) -> str:
    start = f"{start_day} {_MONTHS[start_month - 1]}"
    end = f"{end_day} {_MONTHS[end_month - 1]}"
    return start if start == end else f"{start} – {end}"


def covers(season: BusinessSeason, day: date) -> bool:
    """Does this yearly season include `day`? Handles seasons that cross New Year."""
    start = (season.start_month, season.start_day)
    end = (season.end_month, season.end_day)
    today = (day.month, day.day)
    if start <= end:
        return start <= today <= end
    return today >= start or today <= end  # e.g. 1 Dec – 5 Jan


def _direction(pct: Decimal | None) -> str | None:
    if pct is None:
        return None
    return "busier" if pct > 0 else "quieter" if pct < 0 else "normal"


def _out(s: BusinessSeason) -> SeasonOut:
    return SeasonOut(
        id=s.id,
        name=s.name,
        start=DayOfYear(month=s.start_month, day=s.start_day),
        end=DayOfYear(month=s.end_month, day=s.end_day),
        label=_label(s.start_month, s.start_day, s.end_month, s.end_day),
        crosses_new_year=(s.end_month, s.end_day) < (s.start_month, s.start_day),
        expected_change_pct=s.expected_change_pct,
        direction=_direction(s.expected_change_pct),
        source=s.source,
        status=s.status,
        notes=s.notes,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _to_columns(fields: dict[str, Any]) -> dict[str, Any]:
    columns = dict(fields)
    for which in ("start", "end"):
        value = columns.pop(which, None)
        if value is not None:
            columns[f"{which}_month"] = value["month"]
            columns[f"{which}_day"] = value["day"]
    return columns


def _ordered(stmt):
    # Suggestions first (they need a decision), then in calendar order.
    return stmt.order_by(
        case((BusinessSeason.status == "suggested", 0), else_=1),
        BusinessSeason.start_month,
        BusinessSeason.start_day,
        BusinessSeason.name,
    )


def list_seasons(db: Session, status: str | None = None) -> list[SeasonOut]:
    stmt = select(BusinessSeason)
    if status is not None:
        stmt = stmt.where(BusinessSeason.status == status)
    else:
        stmt = stmt.where(BusinessSeason.status != "dismissed")
    return [_out(s) for s in db.scalars(_ordered(stmt))]


def seasons_on(db: Session, day: date) -> list[SeasonOut]:
    """Active seasons that include `day` (user-entered or confirmed; never mere suggestions)."""
    active = db.scalars(_ordered(select(BusinessSeason).where(BusinessSeason.status == "active")))
    return [_out(s) for s in active if covers(s, day)]


def _get(db: Session, season_id: uuid.UUID) -> BusinessSeason:
    season = db.scalar(select(BusinessSeason).where(BusinessSeason.id == season_id))
    if season is None:
        raise NotFoundError("Season not found", code="season_not_found")
    return season


def _audit(db, tenant, action, season, meta, details):
    record_audit(
        db,
        action,
        actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id,
        target_type="season",
        target_id=season.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        details=details,
    )


def create_season(db: Session, tenant, body: SeasonCreate, meta: RequestMeta) -> SeasonOut:
    season = BusinessSeason(**_to_columns(body.model_dump()), source="user", status="active")
    db.add(season)
    db.flush()
    _audit(db, tenant, AuditAction.SEASON_CREATED, season, meta, None)
    db.commit()
    db.refresh(season)
    return _out(season)


def update_season(
    db: Session, tenant, season_id: uuid.UUID, body: SeasonPatch, meta: RequestMeta
) -> SeasonOut:
    season = _get(db, season_id)
    changes = _to_columns(body.model_dump(include=body.model_fields_set))
    changed = sorted(k for k, v in changes.items() if getattr(season, k) != v)
    details: dict[str, Any] = {"fields": changed}
    if "status" in changed:
        details["status"] = {"from": season.status, "to": changes["status"]}
    for name in changed:
        setattr(season, name, changes[name])
    if changed:
        _audit(db, tenant, AuditAction.SEASON_UPDATED, season, meta, details)
        db.commit()
        db.refresh(season)
    return _out(season)
