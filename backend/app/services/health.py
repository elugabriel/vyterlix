"""Business health: turn a business's KPIs into one explained score per month.

`calculate` reads the KPI values the KPI engine has stored (it never recalculates them), judges
each one with the health rules (data, not code: app/models/health.py), and saves for every
finished month: an overall score, a score and trend per area, and a plain-English explanation
with the evidence behind each number. It is run right after the KPIs are worked out, so the two
are always consistent.

Only finished months get a health score: a month still in progress has only part of its sales,
and judging it would always look like a bad month.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.health import scoring
from app.integrations.base import utcnow
from app.kpi import periods
from app.models.business import BusinessProfile
from app.models.health import (
    BusinessHealth,
    BusinessHealthComponent,
    HealthCategoryWeight,
    HealthRule,
)
from app.models.kpi import KpiDefinition, KpiValue
from app.schemas.health import (
    ComponentOut,
    HealthHistoryOut,
    HealthOut,
    HealthPointOut,
    MetricOut,
)

logger = logging.getLogger("vyterlix.health")
ZERO = Decimal("0")
GRANULARITY = "month"


@dataclass
class _Point:
    """One KPI in one period, as the KPI engine stored it."""

    value: Decimal | None
    status: str
    quality: int | None


@dataclass
class _Metric:
    rule: HealthRule
    score: Decimal
    detail: dict
    quality: int | None


@dataclass
class _Component:
    category: str
    weight: Decimal
    score: Decimal | None = None
    metrics: list[_Metric] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# --- which rules apply ---------------------------------------------------------------------


def _industry(db: Session) -> str | None:
    return db.scalar(select(BusinessProfile.industry_code))


def active_rules(db: Session, industry: str | None) -> list[HealthRule]:
    """The general rules, with any rule written for this industry replacing the general one."""
    rules = db.scalars(select(HealthRule).where(HealthRule.is_active.is_(True))).all()
    chosen: dict[str, HealthRule] = {}
    for rule in rules:
        if rule.industry_code is None:
            chosen.setdefault(rule.kpi_code, rule)
    for rule in rules:
        if industry is not None and rule.industry_code == industry:
            chosen[rule.kpi_code] = rule
    return sorted(chosen.values(), key=lambda r: (r.category, r.kpi_code))


def category_weights(db: Session, industry: str | None) -> dict[str, Decimal]:
    rows = db.scalars(select(HealthCategoryWeight)).all()
    weights = {r.category: Decimal(r.weight) for r in rows if r.industry_code is None}
    for row in rows:
        if industry is not None and row.industry_code == industry:
            weights[row.category] = Decimal(row.weight)
    return {category: weight for category, weight in weights.items() if weight > 0}


# --- judging one month ---------------------------------------------------------------------


def _series(db: Session, codes: set[str]) -> tuple[dict, dict]:
    """{kpi code: {period start: _Point}} for finished months, and {kpi code: (name, unit)}."""
    rows = db.execute(
        select(KpiDefinition.code, KpiDefinition.name, KpiDefinition.unit, KpiValue)
        .join(KpiValue, KpiValue.kpi_id == KpiDefinition.id)
        .where(
            KpiDefinition.code.in_(codes),
            KpiValue.granularity == GRANULARITY,
            KpiValue.is_complete.is_(True),
        )
    ).all()
    series: dict[str, dict[date, _Point]] = {}
    info: dict[str, tuple[str, str]] = {}
    for code, name, unit, value in rows:
        info[code] = (name, unit)
        series.setdefault(code, {})[value.period_start] = _Point(
            None if value.value is None else Decimal(value.value), value.status, value.data_quality
        )
    return series, info


def _judge(
    rule: HealthRule, period: date, series: dict, info: dict
) -> tuple[_Metric | None, str | None]:
    """Score one rule for one month. Returns (metric, None) or (None, why it was left out)."""
    name, unit = info.get(rule.kpi_code, (rule.kpi_code, "count"))
    point = series.get(rule.kpi_code, {}).get(period)
    if point is None or point.status != "ok" or point.value is None:
        return None, f"{name} can't be worked out for this month"
    value = point.value
    baseline = compared = None
    months = 0
    judged = value
    if rule.basis == "vs_baseline":
        earlier = [
            series.get(rule.kpi_code, {}).get(periods.shift(period, GRANULARITY, -n))
            for n in range(1, scoring.BASELINE_PERIODS + 1)
        ]
        usable = [
            p.value for p in earlier if p is not None and p.status == "ok" and p.value is not None
        ]
        baseline = scoring.baseline_of(usable)
        if baseline is None:
            return None, f"there isn't enough history yet to know what is usual for {name}"
        compared = scoring.percent_from(value, baseline)
        if compared is None:
            return None, f"{name} has no usual level to compare with"
        months, judged = len(usable), compared
    score = scoring.score_metric(
        judged,
        rule.direction,
        Decimal(rule.threshold_bad),
        Decimal(rule.threshold_ok),
        Decimal(rule.threshold_good),
    )
    text = scoring.describe_metric(
        name, unit, rule.basis, rule.direction, value, score,
        bad=Decimal(rule.threshold_bad), good=Decimal(rule.threshold_good),
        baseline=baseline, compared=compared, baseline_months=months,
    )  # fmt: skip
    detail = {
        "kpi_code": rule.kpi_code,
        "name": name,
        "unit": unit,
        "basis": rule.basis,
        "value": str(value.quantize(Decimal("0.01"))),
        "baseline": None if baseline is None else str(baseline.quantize(Decimal("0.01"))),
        "baseline_months": months,
        "compared_pct": None if compared is None else str(compared.quantize(Decimal("0.1"))),
        "score": float(score.quantize(Decimal("0.1"))),
        "weight": float(rule.weight),
        "text": text,
    }
    return _Metric(rule, score, detail, point.quality), None


def _evaluate(
    period: date, rules: list[HealthRule], weights: dict[str, Decimal], series: dict, info: dict
) -> dict[str, _Component]:
    components = {c: _Component(c, w) for c, w in weights.items()}
    for rule in rules:
        component = components.get(rule.category)
        if component is None:  # an area that carries no weight is not part of the score
            continue
        metric, why = _judge(rule, period, series, info)
        if metric is None:
            component.skipped.append(why)
        else:
            component.metrics.append(metric)
    for component in components.values():
        average = scoring.weighted_average(
            [(m.score, Decimal(m.rule.weight)) for m in component.metrics]
        )
        component.score = average
    return components


# --- calculating and saving ----------------------------------------------------------------


@dataclass
class HealthRun:
    months: int  # finished months judged
    scored: int  # of those, how many got an overall score


def calculate(db: Session, tenant, *, today: date | None = None) -> HealthRun:
    """Work out and save the health of every finished month the KPIs cover (replacing earlier
    answers). Needs the KPI engine to have run; with no KPI values it saves nothing."""
    organization_id = tenant.organization_id
    industry = _industry(db)
    rules = active_rules(db, industry)
    weights = category_weights(db, industry)
    series, info = _series(db, {r.kpi_code for r in rules})
    months = sorted({p for per in series.values() for p in per})
    if not months:
        return HealthRun(0, 0)

    calculated_at = utcnow()
    db.execute(delete(BusinessHealth).where(BusinessHealth.granularity == GRANULARITY))
    previous_scores: dict[str, int | None] = {}
    previous_overall: int | None = None
    previous_month: date | None = None
    scored = 0
    total_weight = sum(weights.values(), ZERO)
    for month in months:
        components = _evaluate(month, rules, weights, series, info)
        adjacent = (
            previous_month is not None and periods.shift(month, GRANULARITY, -1) == previous_month
        )
        counted = [c for c in components.values() if c.score is not None]
        scored_weight = sum((c.weight for c in counted), ZERO)
        coverage = scoring.to_int(scored_weight / total_weight * 100) if total_weight else 0
        overall = None
        if counted and coverage >= scoring.MIN_COVERAGE:
            overall = scoring.to_int(
                scoring.weighted_average([(c.score, c.weight) for c in counted])
            )
        qualities = [
            m.quality for c in components.values() for m in c.metrics if m.quality is not None
        ]

        component_out = []
        for component in sorted(components.values(), key=lambda c: -c.weight):
            score = None if component.score is None else scoring.to_int(component.score)
            before = previous_scores.get(component.category) if adjacent else None
            contribution = (
                None
                if component.score is None or overall is None or not scored_weight
                else (component.score * component.weight / scored_weight).quantize(Decimal("0.01"))
            )
            details = [m.detail for m in sorted(component.metrics, key=lambda m: m.score)]
            component_out.append(
                {
                    "category": component.category,
                    "score": score,
                    "status": scoring.status_for(score),
                    "previous_score": before,
                    "trend": scoring.trend_for(score, before),
                    "weight": component.weight,
                    "contribution": contribution,
                    "explanation": scoring.explain_component(
                        component.category, score, details, component.skipped
                    ),
                    "details": details,
                }
            )
        unscored = [c["category"] for c in component_out if c["score"] is None]
        before_overall = previous_overall if adjacent else None
        health = BusinessHealth(
            organization_id=organization_id,
            granularity=GRANULARITY,
            period_start=month,
            period_end=periods.end_of(month, GRANULARITY),
            is_complete=True,
            overall_score=overall,
            status=scoring.status_for(overall),
            previous_score=before_overall,
            trend=scoring.trend_for(overall, before_overall),
            coverage_pct=coverage,
            data_quality=min(qualities) if qualities else None,
            explanation=scoring.explain_overall(overall, coverage, component_out, unscored),
            calculated_at=calculated_at,
        )
        db.add(health)
        db.flush()
        for row in component_out:
            db.add(
                BusinessHealthComponent(organization_id=organization_id, health_id=health.id, **row)
            )
        scored += overall is not None
        previous_scores = {c["category"]: c["score"] for c in component_out}
        previous_overall, previous_month = overall, month
    db.commit()
    return HealthRun(len(months), scored)


# --- reading -------------------------------------------------------------------------------


def _out(health: BusinessHealth, components: list[BusinessHealthComponent]) -> HealthOut:
    return HealthOut(
        period_start=health.period_start,
        period_end=health.period_end,
        overall_score=health.overall_score,
        status=health.status,
        previous_score=health.previous_score,
        trend=health.trend,
        coverage_pct=health.coverage_pct,
        data_quality=health.data_quality,
        explanation=health.explanation,
        calculated_at=health.calculated_at,
        components=[
            ComponentOut(
                category=c.category,
                label=scoring.CATEGORY_LABEL[c.category],
                score=c.score,
                status=c.status,
                previous_score=c.previous_score,
                trend=c.trend,
                weight=float(c.weight),
                contribution=None if c.contribution is None else float(c.contribution),
                explanation=c.explanation,
                metrics=[MetricOut(**d) for d in c.details],
            )
            for c in components
        ],
    )


def latest_health(db: Session) -> HealthOut | None:
    """The most recent finished month, with every area and the evidence behind it."""
    health = db.scalars(
        select(BusinessHealth)
        .where(BusinessHealth.granularity == GRANULARITY)
        .order_by(BusinessHealth.period_start.desc())
        .limit(1)
    ).first()
    return None if health is None else _health_for(db, health)


def health_for_month(db: Session, month: date) -> HealthOut:
    health = db.scalars(
        select(BusinessHealth).where(
            BusinessHealth.granularity == GRANULARITY, BusinessHealth.period_start == month
        )
    ).first()
    if health is None:
        raise NotFoundError("No health score for that month", code="health_not_found")
    return _health_for(db, health)


def _health_for(db: Session, health: BusinessHealth) -> HealthOut:
    components = db.scalars(
        select(BusinessHealthComponent)
        .where(BusinessHealthComponent.health_id == health.id)
        .order_by(BusinessHealthComponent.weight.desc(), BusinessHealthComponent.category)
    ).all()
    return _out(health, list(components))


def health_history(db: Session, limit: int = 24) -> HealthHistoryOut:
    rows = db.scalars(
        select(BusinessHealth)
        .where(BusinessHealth.granularity == GRANULARITY)
        .order_by(BusinessHealth.period_start.desc())
        .limit(limit)
    ).all()
    return HealthHistoryOut(
        points=[
            HealthPointOut(
                period_start=h.period_start,
                overall_score=h.overall_score,
                status=h.status,
                trend=h.trend,
                coverage_pct=h.coverage_pct,
            )
            for h in reversed(rows)
        ]
    )
