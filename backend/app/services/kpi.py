"""The KPI engine: work out every active KPI for every period a business has data for.

Flow of `calculate`:
  1. read the KPI definitions (data, not code) and parse each stored formula safely;
  2. ask the SQL measures (app/kpi/measures.py) for every number the formulas read, for every
     period from a little before the first one wanted (so "previous period" and "same period
     last year" have something to look at) to the last;
  3. evaluate each formula for each period, with the previous period's value and the change;
  4. attach how good the data behind each period is (the Phase 4 data-quality score);
  5. save the answers (replacing earlier ones for those periods) and record the run.

A KPI that can't be worked out says so (status "no_data" or "undefined") instead of showing a
made-up number. Everything is for the business the session is scoped to.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.uk import today_uk
from app.integrations.base import utcnow
from app.kpi import periods
from app.kpi.expression import Compiled, ExpressionError, compile_expression, evaluate
from app.kpi.measures import MEASURES, PRESENCE, compute_series, has_records
from app.models.business import BusinessProfile
from app.models.data import Expense, Sale, StockMovement
from app.models.kpi import KpiCalculationRun, KpiDefinition, KpiValue
from app.schemas.kpi import KpiHistoryOut, KpiOut, KpisOut, KpiValueOut, RunOut
from app.services.data_quality import build_report

logger = logging.getLogger("vyterlix.kpi")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
BATCH = 500


# --- presenting numbers ------------------------------------------------------------------------


def _text(value: Decimal | None, unit: str) -> str | None:
    """A decimal string rounded the way people read it: pence and percentages to 2 places,
    counts as whole numbers when they are whole."""
    if value is None:
        return None
    if unit == "count" and value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return str(value.quantize(Decimal("0.01")))


def _input_key(shift: str, name: str) -> str:
    return name if shift == "now" else f"{shift}({name})"


# --- which KPIs apply -----------------------------------------------------------------------------


def active_definitions(db: Session) -> list[KpiDefinition]:
    """Active KPIs for everyone, plus those limited to this business's industry."""
    industry = db.scalar(select(BusinessProfile.industry_code))
    return list(
        db.scalars(
            select(KpiDefinition)
            .where(
                KpiDefinition.is_active.is_(True),
                (KpiDefinition.industry_code.is_(None)) | (KpiDefinition.industry_code == industry),
            )
            .order_by(KpiDefinition.category, KpiDefinition.sort_order, KpiDefinition.code)
        )
    )


def _data_bounds(db: Session) -> tuple[date, date] | None:
    """The first and last date the business has any sales, expenses or stock movements on."""
    lows, highs = [], []
    for column in (Sale.sold_on, Expense.spent_on, StockMovement.moved_on):
        low, high = db.execute(select(func.min(column), func.max(column))).one()
        if low is not None:
            lows.append(low)
            highs.append(high)
    return (min(lows), max(highs)) if lows else None


def _quality_by_period(db: Session, granularity: str, today: date) -> dict[date, int]:
    """The data-quality score of each month, rolled up to the period (average of its months)."""
    months = {m.month: m.score for m in build_report(db, today).months if m.score is not None}
    if granularity == "month":
        return months
    by_period: dict[date, list[int]] = {}
    for month, score in months.items():
        by_period.setdefault(periods.start_of(month, granularity), []).append(score)
    return {p: round(sum(v) / len(v)) for p, v in by_period.items()}


# --- the calculation -----------------------------------------------------------------------------


def _run_out(run: KpiCalculationRun) -> RunOut:
    return RunOut.model_validate(run, from_attributes=True)


def calculate(
    db: Session,
    tenant,
    *,
    granularity: str = "month",
    first: date | None = None,
    last: date | None = None,
    trigger: str = "manual",
    job_id: uuid.UUID | None = None,
    today: date | None = None,
) -> RunOut:
    """Work out and save every KPI for each period from `first` to `last` (default: from the
    business's first record to the period in progress)."""
    today = today or today_uk()
    organization_id, user_id = tenant.organization_id, tenant.user.id
    bounds = _data_bounds(db)
    first = periods.start_of(first or (bounds[0] if bounds else today), granularity)
    last = max(periods.start_of(last or today, granularity), first)

    definitions = active_definitions(db)
    run = KpiCalculationRun(
        trigger=trigger,
        granularity=granularity,
        period_from=first,
        period_to=periods.end_of(last, granularity),
        kpi_count=len(definitions),
        started_at=utcnow(),
        requested_by_user_id=user_id,
        job_id=job_id,
    )
    db.add(run)
    db.commit()
    run_id = run.id

    try:
        written = 0
        if bounds is not None:
            written = _calculate(
                db, organization_id, run_id, definitions, granularity, first, last, today
            )
    except BaseException:
        db.rollback()
        failed = db.get(KpiCalculationRun, run_id)
        failed.status, failed.finished_at = "failed", utcnow()
        failed.error_message = "The KPIs could not be worked out. Please try again."
        db.commit()
        raise
    done = db.get(KpiCalculationRun, run_id)
    done.status, done.finished_at, done.values_written = "succeeded", utcnow(), written
    db.commit()
    return _run_out(done)


def _calculate(
    db: Session,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    definitions: list[KpiDefinition],
    granularity: str,
    first: date,
    last: date,
    today: date,
) -> int:
    compiled: dict[str, Compiled] = {}
    for definition in list(definitions):
        try:
            compiled[definition.code] = compile_expression(definition.expression, set(MEASURES))
        except ExpressionError:
            # One broken formula must not take every other KPI down with it.
            logger.error("KPI %s has an invalid formula; skipped", definition.code, exc_info=True)
            definitions.remove(definition)
    needed = {name for c in compiled.values() for name in c.measures}
    shifts = {s for c in compiled.values() for s in c.shifts}
    before = periods.shift(first, granularity, -1)  # to give `first` a previous value
    earliest = periods.shift(before, granularity, -1) if "prev" in shifts else before
    if "yoy" in shifts:
        earliest = min(earliest, periods.same_period_last_year(before, granularity))
    data = compute_series(db, needed, granularity, earliest, periods.end_of(last, granularity))
    present = {dataset: has_records(db, dataset) for dataset in PRESENCE}
    quality = _quality_by_period(db, granularity, today)

    def evaluate_at(definition: KpiDefinition, period: date) -> tuple[str, Decimal | None, dict]:
        expr = compiled[definition.code]
        targets = {
            "now": period,
            "prev": periods.shift(period, granularity, -1),
            "yoy": periods.same_period_last_year(period, granularity),
        }

        def read(shift: str, name: str) -> Decimal | None:
            return data[name].get(targets[shift], ZERO)

        inputs = {
            _input_key(shift, name): str(read(shift, name).quantize(Decimal("0.0001")))
            for shift, name in sorted(expr.reads)
        }
        if not all(present.get(dataset, False) for dataset in definition.requires):
            return "no_data", None, inputs
        value = evaluate(expr, read)
        return ("ok", value, inputs) if value is not None else ("undefined", None, inputs)

    calculated_at = utcnow()
    rows, results = [], {}
    for period in periods.series(before, last, granularity):
        for definition in definitions:
            results[(definition.code, period)] = evaluate_at(definition, period)
    for period in periods.series(first, last, granularity):
        end = periods.end_of(period, granularity)
        for definition in definitions:
            status, value, inputs = results[(definition.code, period)]
            earlier_status, earlier, _ = results[
                (definition.code, periods.shift(period, granularity, -1))
            ]
            previous = earlier if earlier_status == "ok" else None
            change = None
            if value is not None and previous not in (None, ZERO):
                change = (value - previous) / abs(previous) * HUNDRED
            rows.append(
                {
                    "organization_id": organization_id,
                    "kpi_id": definition.id,
                    "run_id": run_id,
                    "granularity": granularity,
                    "period_start": period,
                    "period_end": end,
                    "is_complete": end < today,
                    "status": status,
                    "value": value,
                    "previous_value": previous,
                    "change_pct": change,
                    "data_quality": quality.get(period),
                    "inputs": inputs,
                    "calculated_at": calculated_at,
                }
            )
    for start in range(0, len(rows), BATCH):
        statement = pg_insert(KpiValue).values(rows[start : start + BATCH])
        db.execute(
            statement.on_conflict_do_update(
                constraint="uq_kpi_values_period",
                set_={
                    column: statement.excluded[column]
                    for column in rows[0]
                    if column not in ("organization_id", "kpi_id", "granularity", "period_start")
                },
            )
        )
    db.commit()
    return len(rows)


# --- reading -------------------------------------------------------------------------------------


def _value_out(row: KpiValue, unit: str) -> KpiValueOut:
    return KpiValueOut(
        period_start=row.period_start,
        period_end=row.period_end,
        is_complete=row.is_complete,
        status=row.status,
        value=_text(row.value, unit),
        previous_value=_text(row.previous_value, unit),
        change_pct=_text(row.change_pct, "percent"),
        data_quality=row.data_quality,
        inputs=row.inputs,
    )


def last_run(db: Session) -> RunOut | None:
    run = db.scalars(
        select(KpiCalculationRun).order_by(KpiCalculationRun.started_at.desc()).limit(1)
    ).first()
    return _run_out(run) if run else None


def list_kpis(db: Session, granularity: str = "month") -> KpisOut:
    definitions = active_definitions(db)
    latest_period = db.scalar(
        select(func.max(KpiValue.period_start)).where(
            KpiValue.granularity == granularity, KpiValue.is_complete.is_(True)
        )
    )
    current_period = db.scalar(
        select(func.max(KpiValue.period_start)).where(
            KpiValue.granularity == granularity, KpiValue.is_complete.is_(False)
        )
    )
    wanted = [p for p in (latest_period, current_period) if p is not None]
    by_key: dict[tuple[uuid.UUID, date], KpiValue] = {}
    if wanted:
        for value in db.scalars(
            select(KpiValue).where(
                KpiValue.granularity == granularity, KpiValue.period_start.in_(wanted)
            )
        ):
            by_key[(value.kpi_id, value.period_start)] = value

    def pick(definition: KpiDefinition, period: date | None) -> KpiValueOut | None:
        row = by_key.get((definition.id, period)) if period else None
        return _value_out(row, definition.unit) if row else None

    return KpisOut(
        granularity=granularity,
        kpis=[
            KpiOut(
                code=d.code,
                name=d.name,
                description=d.description,
                category=d.category,
                unit=d.unit,
                direction=d.direction,
                requires=list(d.requires),
                latest=pick(d, latest_period),
                current=pick(d, current_period),
            )
            for d in definitions
        ],
        last_run=last_run(db),
    )


def kpi_history(
    db: Session, code: str, granularity: str = "month", limit: int = 24
) -> KpiHistoryOut:
    definition = next((d for d in active_definitions(db) if d.code == code), None)
    if definition is None:
        raise NotFoundError("KPI not found", code="kpi_not_found")
    rows = db.scalars(
        select(KpiValue)
        .where(KpiValue.kpi_id == definition.id, KpiValue.granularity == granularity)
        .order_by(KpiValue.period_start.desc())
        .limit(limit)
    ).all()
    return KpiHistoryOut(
        code=definition.code,
        name=definition.name,
        description=definition.description,
        category=definition.category,
        unit=definition.unit,
        direction=definition.direction,
        requires=list(definition.requires),
        granularity=granularity,
        formula=definition.expression,
        values=[_value_out(r, definition.unit) for r in reversed(rows)],
    )
