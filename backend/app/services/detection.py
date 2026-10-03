"""Detection: notice the changes in a business's figures that are worth a look.

`detect` reads the monthly KPI values the KPI engine stored (it never recalculates them) and
saves a `DetectionEvent` for every finished month in which a figure moved by more than is normal
(rules in app/diagnostics/detection.py). It runs straight after the KPIs are worked out, so the
two always agree.

Where the owner has confirmed busy and quiet seasons, a change those seasons lead you to expect
is still shown but marked as expected, so a normal seasonal dip is not reported as a problem.

Re-running is safe: an event that is still true is updated in place (keeping what anyone has
done with it, such as dismissing it); one that is no longer true is removed.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.diagnostics import detection as rules
from app.integrations.base import utcnow
from app.kpi import periods
from app.models.diagnostics import DetectionEvent
from app.models.health import HealthRule
from app.models.kpi import KpiDefinition, KpiValue
from app.schemas.diagnostics import DetectionOut
from app.services.health import GRANULARITY, confirmed_seasons, season_effect

MATERIAL = "material_change"
SEVERITY_ORDER = {"major": 0, "notable": 1}


@dataclass
class DetectionRun:
    months: int  # finished months looked at
    found: int  # events saved
    removed: int  # earlier events that are no longer true


def _month_name(month: date) -> str:
    return f"{month:%B %Y}"


def _seasonal_codes(db: Session) -> set[str]:
    """The figures that follow the trading year (the same ones the health score treats so)."""
    return set(
        db.scalars(
            select(HealthRule.kpi_code).where(
                HealthRule.seasonal.is_(True), HealthRule.is_active.is_(True)
            )
        )
    )


def detect(db: Session, tenant) -> DetectionRun:
    """Find and save the material changes in every finished month (replacing earlier answers)."""
    organization_id = tenant.organization_id
    rows = db.execute(
        select(KpiDefinition, KpiValue)
        .join(KpiValue, KpiValue.kpi_id == KpiDefinition.id)
        .where(
            KpiDefinition.is_active.is_(True),
            KpiValue.granularity == GRANULARITY,
            KpiValue.is_complete.is_(True),
            KpiValue.value.is_not(None),
            KpiValue.previous_value.is_not(None),
        )
    ).all()
    seasons = confirmed_seasons(db)
    seasonal = _seasonal_codes(db) if seasons else set()
    detected_at = utcnow()

    wanted: dict[tuple[uuid.UUID, date], dict] = {}
    months: set[date] = set()
    for definition, kv in rows:
        months.add(kv.period_start)
        value, previous = Decimal(kv.value), Decimal(kv.previous_value)
        measured = rules.measure_change(definition.unit, value, previous)
        if measured is None:
            continue
        change, change_unit = measured
        severity = rules.severity_for(change, change_unit)
        if severity is None:
            continue
        month = kv.period_start
        before = periods.shift(month, GRANULARITY, -1)
        expected = None
        explained = False
        if definition.code in seasonal and change_unit == "percent":
            expected = rules.expected_season_change(
                season_effect(seasons, month), season_effect(seasons, before)
            )
            explained = rules.season_explains(change, expected)
        wanted[(definition.id, month)] = {
            "organization_id": organization_id,
            "kpi_id": definition.id,
            "kind": MATERIAL,
            "granularity": GRANULARITY,
            "period_start": month,
            "period_end": kv.period_end,
            "direction": "up" if change > 0 else "down",
            "severity": severity,
            "effect": rules.effect_for(definition.direction, change),
            "value": value,
            "reference_value": previous,
            "change": change.quantize(Decimal("0.0001")),
            "change_unit": change_unit,
            "explained_by_season": explained,
            "data_quality": kv.data_quality,
            "summary": rules.describe(
                definition.name,
                definition.unit,
                _month_name(month),
                _month_name(before),
                value,
                previous,
                change,
                change_unit,
                explained_by_season=explained,
            ),  # fmt: skip
            "details": {
                "kpi_code": definition.code,
                "unit": definition.unit,
                "compared_with": before.isoformat(),
                "expected_season_change_pct": None if expected is None else str(round(expected, 1)),
            },
            "detected_at": detected_at,
        }

    existing = {
        (e.kpi_id, e.period_start): e
        for e in db.scalars(select(DetectionEvent).where(DetectionEvent.kind == MATERIAL))
    }
    removed = 0
    for key, event in existing.items():
        if key not in wanted:
            db.delete(event)
            removed += 1
    for key, values in wanted.items():
        event = existing.get(key)
        if event is None:
            db.add(DetectionEvent(**values))
        else:
            for field, new in values.items():
                setattr(event, field, new)
    db.commit()
    return DetectionRun(len(months), len(wanted), removed)


# --- reading -------------------------------------------------------------------------------


def _out(event: DetectionEvent, definition: KpiDefinition) -> DetectionOut:
    return DetectionOut(
        id=event.id,
        kpi_code=definition.code,
        kpi_name=definition.name,
        category=definition.category,
        unit=definition.unit,
        kind=event.kind,
        period_start=event.period_start,
        period_end=event.period_end,
        direction=event.direction,
        severity=event.severity,
        effect=event.effect,
        value=str(event.value.quantize(Decimal("0.01"))),
        reference_value=str(event.reference_value.quantize(Decimal("0.01"))),
        change=str(event.change.quantize(Decimal("0.1"))),
        change_unit=event.change_unit,
        explained_by_season=event.explained_by_season,
        data_quality=event.data_quality,
        summary=event.summary,
        status=event.status,
        detected_at=event.detected_at,
    )


def list_events(
    db: Session,
    *,
    month: date | None = None,
    effect: str | None = None,
    severity: str | None = None,
    include_expected: bool = True,
    limit: int = 100,
) -> list[DetectionOut]:
    """Newest month first; within a month, the biggest changes first."""
    query = select(DetectionEvent, KpiDefinition).join(
        KpiDefinition, KpiDefinition.id == DetectionEvent.kpi_id
    )
    if month is not None:
        query = query.where(DetectionEvent.period_start == month)
    if effect is not None:
        query = query.where(DetectionEvent.effect == effect)
    if severity is not None:
        query = query.where(DetectionEvent.severity == severity)
    if not include_expected:
        query = query.where(DetectionEvent.explained_by_season.is_(False))
    found = db.execute(query).all()
    found.sort(
        key=lambda pair: (
            -pair[0].period_start.toordinal(),
            SEVERITY_ORDER[pair[0].severity],
            -abs(pair[0].change),
            pair[1].code,
        )
    )
    return [_out(event, definition) for event, definition in found[:limit]]


def get_event(db: Session, event_id: uuid.UUID) -> DetectionOut:
    row = db.execute(
        select(DetectionEvent, KpiDefinition)
        .join(KpiDefinition, KpiDefinition.id == DetectionEvent.kpi_id)
        .where(DetectionEvent.id == event_id)
    ).first()
    if row is None:
        raise NotFoundError("That change was not found", code="detection_not_found")
    return _out(*row)
