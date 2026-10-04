"""Forecasting: what a business's figures are likely to do over the next few months.

`calculate` reads the monthly KPI values the KPI engine stored (it never recalculates them), tries
each forecasting method on the business's own recent history, keeps the one that was closest, and
saves a forecast with a range around every prediction. It runs straight after the KPIs are worked
out, so the forecast always matches the latest figures.

The numbers come only from the statistical methods in app/forecast. A language model is never
asked for a forecast; it may be asked later to put one into words.

Where the owner has confirmed busy and quiet seasons, a figure that follows the trading year
(sales, profit, customers) is learned with each month's season taken out and put back into the
forecast months, so a normal Christmas rush does not look like growth.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.forecast import accuracy as accuracy_rules
from app.forecast import selection
from app.forecast.models import METHODS
from app.health import scoring
from app.health.scoring import format_value
from app.integrations.base import utcnow
from app.kpi import periods
from app.models.forecast import Forecast, ForecastEvaluation, ForecastModel, ForecastPrediction
from app.models.health import HealthRule
from app.models.kpi import KpiDefinition, KpiValue
from app.schemas.forecast import (
    AccuracyOut,
    AccuracyRowOut,
    AheadOut,
    EvaluationOut,
    ForecastOut,
    HistoryPointOut,
    MethodOut,
    PredictionOut,
)
from app.services.health import confirmed_seasons, season_effect

GRANULARITY = "month"
PENNY = Decimal("0.01")
HISTORY_SHOWN = 24
# The figures that can be forecast, and whether they can go below nothing.
FORECASTABLE = {"revenue": {"non_negative": True}}
DEFAULT_HORIZON = 3


@dataclass
class ForecastRun:
    forecasts: int
    forecast_months: int


def forecastable() -> list[str]:
    return list(FORECASTABLE)


def _series(db: Session, kpi: KpiDefinition) -> list[tuple[date, Decimal]]:
    """The finished months with a figure, oldest first, as one unbroken run ending at the latest
    (a gap means the months before it say little about what comes next)."""
    rows = db.scalars(
        select(KpiValue).where(
            KpiValue.kpi_id == kpi.id,
            KpiValue.granularity == GRANULARITY,
            KpiValue.is_complete.is_(True),
            KpiValue.status == "ok",
            KpiValue.value.is_not(None),
        )
    ).all()
    found = {r.period_start: Decimal(r.value) for r in rows}
    if not found:
        return []
    month = max(found)
    run = []
    while month in found:
        run.append((month, found[month]))
        month = periods.shift(month, GRANULARITY, -1)
    return list(reversed(run))


def _follows_the_year(db: Session, code: str) -> bool:
    return bool(
        db.scalar(
            select(HealthRule.id).where(
                HealthRule.kpi_code == code,
                HealthRule.seasonal.is_(True),
                HealthRule.is_active.is_(True),
            )
        )
    )


def _explain(
    name: str, unit: str, predictions, score: selection.Score, method_name: str, level: int
) -> str:
    first, rest = predictions[0], predictions[1:]
    month = f"{first['period_start']:%B %Y}"
    text = (
        f"We expect {name} of about {format_value(first['value'], unit)} in {month}, most likely "
        f"between {format_value(first['lower'], unit)} and {format_value(first['upper'], unit)}."
    )
    if rest:
        later = ", ".join(
            f"{format_value(p['value'], unit)} in {p['period_start']:%B %Y}" for p in rest
        )
        text += f" After that: {later}."
    miss = f"about {format_value(Decimal(str(score.mae)), unit)}"
    if score.mape is not None:
        miss += f" ({score.mape:.0f}%)"
    return (
        f"{text} The range should hold {level} times out of 100, and it widens the further ahead "
        f'we look. The method is "{method_name}", the closest of those we tried on your last '
        f"{score.n_points} months, where it missed by {miss} on average."
    )


def calculate(
    db: Session,
    tenant,
    kpi_code: str = "revenue",
    horizon: int = DEFAULT_HORIZON,
    level: int = selection.INTERVAL_LEVEL,
    as_of: date | None = None,
) -> ForecastOut:
    """Forecast one figure for the next `horizon` months and save it (replacing any forecast
    made from the same last month). `as_of` forecasts as if standing at the end of that earlier
    month, using only what was known then; it is how past forecasts are made to check them against
    what happened."""
    if kpi_code not in FORECASTABLE:
        raise AppError("That figure can't be forecast yet.", code="bad_metric", status_code=422)
    if not 1 <= horizon <= 12:
        raise AppError(
            "Forecast between 1 and 12 months ahead.", code="bad_horizon", status_code=422
        )
    if level not in selection.Z:
        raise AppError(
            "Choose a range of 50, 60, 70, 80, 90, 95 or 99.", code="bad_level", status_code=422
        )
    kpi = db.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).one()
    non_negative = FORECASTABLE[kpi_code]["non_negative"]
    refresh_actuals(db, tenant)  # note what really happened in months forecast earlier
    series = _series(db, kpi)
    if as_of is not None:
        series = [(m, v) for m, v in series if m <= as_of]
    if not series:
        raise NotFoundError(
            "There are no finished months of figures to forecast from yet.", code="no_history"
        )
    as_of = series[-1][0]
    months = [m for m, _ in series]
    values = [float(v) for _, v in series]
    target_months = [periods.shift(as_of, GRANULARITY, step) for step in range(1, horizon + 1)]

    # Take each month's season out (and put it back into the forecast months)
    seasons = confirmed_seasons(db) if _follows_the_year(db, kpi_code) else []
    factors = {m: 1.0 for m in months + target_months}
    adjusted = False
    if seasons:
        wanted = {m: scoring.season_factor(season_effect(seasons, m)) for m in factors}
        if all(f is not None for f in wanted.values()):
            factors = {m: float(f) for m, f in wanted.items()}
            adjusted = any(f != 1.0 for f in factors.values())
    history = [v / factors[m] for m, v in zip(months, values, strict=True)]

    candidates = selection.eligible(history)
    scores = [selection.backtest(m, history, non_negative) for m in candidates]
    best = selection.choose(scores)

    db.execute(delete(Forecast).where(Forecast.kpi_id == kpi.id, Forecast.as_of == as_of))
    db.flush()
    now = utcnow()
    organization_id = tenant.organization_id
    if best is None:
        forecast = Forecast(
            organization_id=organization_id, kpi_id=kpi.id, as_of=as_of, horizon=horizon,
            status="insufficient_data", interval_level=level, history_months=len(series),
            adjusted_for_seasons=adjusted, calculated_at=now,
            explanation=(
                f"We need at least {selection.MIN_HISTORY} finished months of {kpi.name} to "
                f"forecast it, and have {len(series)} so far."
            ),
            inputs={"months": [m.isoformat() for m in months]},
        )  # fmt: skip
        db.add(forecast)
        db.commit()
        return _out(db, forecast, kpi)

    method = METHODS[best.code]
    model = db.scalars(select(ForecastModel).where(ForecastModel.code == best.code)).one()
    sigma = selection.spread(best, history)
    raw = method.predict(history, horizon)
    predictions = []
    for step, (target, point) in enumerate(zip(target_months, raw, strict=True), start=1):
        point = max(0.0, point) if non_negative else point
        lower, upper = selection.interval(point, sigma, step, level, non_negative)
        factor = factors[target]
        predictions.append(
            {
                "period_start": target,
                "period_end": periods.end_of(target, GRANULARITY),
                "value": Decimal(str(point * factor)).quantize(PENNY),
                "lower": Decimal(str(lower * factor)).quantize(PENNY),
                "upper": Decimal(str(upper * factor)).quantize(PENNY),
            }
        )
    forecast = Forecast(
        organization_id=organization_id, kpi_id=kpi.id, as_of=as_of, horizon=horizon,
        status="ok", model_id=model.id, model_version=model.version, interval_level=level,
        history_months=len(series), adjusted_for_seasons=adjusted, calculated_at=now,
        explanation=_explain(kpi.name, kpi.unit, predictions, best, model.name, level),
        inputs={
            "months": [m.isoformat() for m in months],
            "values": [str(v) for _, v in series],
            "sigma": round(sigma, 4),
            "non_negative": non_negative,
        },
    )  # fmt: skip
    db.add(forecast)
    db.flush()
    for p in predictions:
        db.add(
            ForecastPrediction(
                organization_id=organization_id, forecast_id=forecast.id,
                period_start=p["period_start"], period_end=p["period_end"], value=p["value"],
                lower_value=p["lower"], upper_value=p["upper"],
            )
        )  # fmt: skip
    for score in scores:
        db.add(
            ForecastEvaluation(
                organization_id=organization_id, forecast_id=forecast.id, model_code=score.code,
                method="backtest", mae=Decimal(str(score.mae)).quantize(Decimal("0.000001")),
                rmse=Decimal(str(score.rmse)).quantize(Decimal("0.000001")),
                mape=_decimal(score.mape, "0.0001"),
                n_points=score.n_points, is_chosen=score.code == best.code,
            )
        )  # fmt: skip
    db.commit()
    return _out(db, forecast, kpi)


def _decimal(value: float | None, places: str) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal(places))


def calculate_all(db: Session, tenant) -> ForecastRun:
    """Forecast every figure that can be forecast, for those with any history. Used straight
    after the KPIs are worked out. (Each forecast first notes what really happened in months
    forecast earlier.)"""
    done = months = 0
    for code in FORECASTABLE:
        try:
            result = calculate(db, tenant, code)
        except NotFoundError:
            continue
        done += 1
        months += len(result.predictions)
    return ForecastRun(done, months)


# --- checking forecasts against what happened -----------------------------------------------


def refresh_actuals(db: Session, tenant) -> int:
    """Fill in the real figure for every forecast month that has since finished, and score each
    forecast on the months that can now be checked. Safe to run again: a figure that has changed
    (a corrected import, say) is updated. Returns how many predictions were newly filled in or
    changed."""
    organization_id = tenant.organization_id
    rows = db.execute(
        select(ForecastPrediction, Forecast, KpiValue)
        .join(
            Forecast,
            (Forecast.organization_id == ForecastPrediction.organization_id)
            & (Forecast.id == ForecastPrediction.forecast_id),
        )
        .join(
            KpiValue,
            (KpiValue.kpi_id == Forecast.kpi_id)
            & (KpiValue.period_start == ForecastPrediction.period_start)
            & (KpiValue.granularity == GRANULARITY),
        )
        .where(
            KpiValue.is_complete.is_(True),
            KpiValue.status == "ok",
            KpiValue.value.is_not(None),
        )
    ).all()
    now = utcnow()
    changed = 0
    touched: dict = {}
    for prediction, forecast, value in rows:
        touched[forecast.id] = forecast
        if prediction.actual_value is None or Decimal(prediction.actual_value) != Decimal(
            value.value
        ):
            prediction.actual_value = Decimal(value.value)
            prediction.actual_recorded_at = now
            changed += 1
    db.flush()
    for forecast in touched.values():
        _score_forecast(db, organization_id, forecast)
    if touched:
        db.commit()
    return changed


def _checked(predictions) -> list[accuracy_rules.Checked]:
    return [
        accuracy_rules.Checked(
            float(p.value), float(p.lower_value), float(p.upper_value), float(p.actual_value)
        )
        for p in predictions
        if p.actual_value is not None
    ]


def _score_forecast(db: Session, organization_id, forecast: Forecast) -> None:
    """Record how one forecast did on the months that have finished (replacing its last score)."""
    predictions = db.scalars(
        select(ForecastPrediction).where(ForecastPrediction.forecast_id == forecast.id)
    ).all()
    result = accuracy_rules.score(_checked(predictions))
    db.execute(
        delete(ForecastEvaluation).where(
            ForecastEvaluation.forecast_id == forecast.id, ForecastEvaluation.method == "actual"
        )
    )
    if result is None or forecast.model_id is None:
        return
    model = db.get(ForecastModel, forecast.model_id)
    db.add(
        ForecastEvaluation(
            organization_id=organization_id,
            forecast_id=forecast.id,
            model_code=model.code,
            method="actual",
            mae=Decimal(str(result.mae)).quantize(Decimal("0.000001")),
            rmse=Decimal(str(result.rmse)).quantize(Decimal("0.000001")),
            mape=_decimal(result.mape, "0.0001"),
            n_points=result.n,
            within_range=result.within,
            is_chosen=True,
        )
    )


def accuracy(db: Session, kpi_code: str) -> AccuracyOut:
    """How well past forecasts of a figure matched what happened, over every forecast month that
    has since finished."""
    if kpi_code not in FORECASTABLE:
        raise AppError("That figure can't be forecast yet.", code="bad_metric", status_code=422)
    kpi = db.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).one()
    found = db.execute(
        select(ForecastPrediction, Forecast)
        .join(
            Forecast,
            (Forecast.organization_id == ForecastPrediction.organization_id)
            & (Forecast.id == ForecastPrediction.forecast_id),
        )
        .where(Forecast.kpi_id == kpi.id, ForecastPrediction.actual_value.is_not(None))
    ).all()
    unit = kpi.unit
    entries = []
    for prediction, forecast in found:
        ahead = (
            (prediction.period_start.year - forecast.as_of.year) * 12
            + prediction.period_start.month
            - forecast.as_of.month
        )
        entries.append((prediction, forecast, ahead))
    entries.sort(key=lambda e: (e[0].period_start, e[2]), reverse=True)

    items = [_checked([p])[0] for p, _, _ in entries]
    result = accuracy_rules.score(items)
    levels = [f.interval_level for _, f, _ in entries]
    promised = max(set(levels), key=levels.count) if levels else selection.INTERVAL_LEVEL
    headline, verdict = accuracy_rules.headline(result, unit, promised, kpi.name.lower())

    by_ahead = []
    for step in sorted({a for _, _, a in entries}):
        part = accuracy_rules.score(
            [i for i, (_, _, a) in zip(items, entries, strict=True) if a == step]
        )
        by_ahead.append(
            AheadOut(
                months_ahead=step,
                checked=part.n,
                typical_miss_pct=None if part.mape is None else f"{part.mape:.1f}",
                within_range_pct=f"{part.within / part.n * 100:.1f}",
            )
        )
    rows = []
    for item, (prediction, forecast, ahead) in zip(items, entries, strict=True):
        error = item.predicted - item.actual
        rows.append(
            AccuracyRowOut(
                period_start=prediction.period_start,
                made_from=forecast.as_of,
                months_ahead=ahead,
                predicted=_money(prediction.value),
                lower=_money(prediction.lower_value),
                upper=_money(prediction.upper_value),
                actual=_money(prediction.actual_value),
                error=_money(Decimal(str(error))),
                error_pct=None if item.actual == 0 else f"{error / abs(item.actual) * 100:.1f}",
                within_range=accuracy_rules.within_range(item),
            )
        )
    enough = result is not None and result.n >= accuracy_rules.MIN_CHECKED
    return AccuracyOut(
        kpi_code=kpi.code,
        kpi_name=kpi.name,
        unit=unit,
        enough_data=enough,
        checked=0 if result is None else result.n,
        within_range=0 if result is None else result.within,
        within_range_pct=None if result is None else f"{result.within / result.n * 100:.1f}",
        promised_pct=promised,
        typical_miss=None if result is None else _money(Decimal(str(result.mae))),
        typical_miss_pct=None if result is None or result.mape is None else f"{result.mape:.1f}",
        bias_pct=None if result is None or result.bias_pct is None else f"{result.bias_pct:.1f}",
        headline=headline,
        verdict=verdict,
        by_months_ahead=by_ahead,
        rows=rows[:HISTORY_SHOWN],
    )


def _money(value) -> str:
    return str(Decimal(value).quantize(PENNY))


# --- reading -------------------------------------------------------------------------------


def read_latest(db: Session, kpi_code: str) -> ForecastOut | None:
    """The most recent forecast of a figure, or None if there has not been one."""
    if kpi_code not in FORECASTABLE:
        raise AppError("That figure can't be forecast yet.", code="bad_metric", status_code=422)
    kpi = db.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).one()
    forecast = db.scalars(
        select(Forecast).where(Forecast.kpi_id == kpi.id).order_by(Forecast.as_of.desc()).limit(1)
    ).first()
    if forecast is None:
        return None
    return _out(db, forecast, kpi)


def _method(model: ForecastModel) -> MethodOut:
    return MethodOut(
        code=model.code, name=model.name, description=model.description, version=model.version
    )


def _out(db: Session, forecast: Forecast, kpi: KpiDefinition) -> ForecastOut:
    models = {m.id: m for m in db.scalars(select(ForecastModel))}
    by_code = {m.code: m for m in models.values()}
    predictions = db.scalars(
        select(ForecastPrediction)
        .where(ForecastPrediction.forecast_id == forecast.id)
        .order_by(ForecastPrediction.period_start)
    ).all()
    evaluations = db.scalars(
        select(ForecastEvaluation)
        .where(ForecastEvaluation.forecast_id == forecast.id)
        .order_by(ForecastEvaluation.mae)
    ).all()
    series = [(m, v) for m, v in _series(db, kpi) if m <= forecast.as_of][-HISTORY_SHOWN:]

    def money(value) -> str:
        return str(Decimal(value).quantize(PENNY))

    return ForecastOut(
        id=forecast.id,
        kpi_code=kpi.code,
        kpi_name=kpi.name,
        unit=kpi.unit,
        status=forecast.status,
        as_of=forecast.as_of,
        horizon=forecast.horizon,
        interval_level=forecast.interval_level,
        history_months=forecast.history_months,
        adjusted_for_seasons=forecast.adjusted_for_seasons,
        method=None if forecast.model_id is None else _method(models[forecast.model_id]),
        explanation=forecast.explanation,
        calculated_at=forecast.calculated_at,
        predictions=[
            PredictionOut(
                period_start=p.period_start,
                period_end=p.period_end,
                value=money(p.value),
                lower=money(p.lower_value),
                upper=money(p.upper_value),
                actual_value=None if p.actual_value is None else money(p.actual_value),
            )
            for p in predictions
        ],
        history=[HistoryPointOut(period_start=m, value=money(v)) for m, v in series],
        evaluations=[
            EvaluationOut(
                method=_method(by_code[e.model_code]),
                kind=e.method,
                typical_miss=money(e.mae),
                typical_miss_pct=None
                if e.mape is None
                else str(Decimal(e.mape).quantize(Decimal("0.1"))),
                months_tested=e.n_points,
                chosen=e.is_chosen,
            )
            for e in evaluations
        ],
    )
