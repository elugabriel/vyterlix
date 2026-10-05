# ruff: noqa: E501
"""Business memory (Phase 12): what is normal for this business, what has been tried and how it
went, and the limits the owner has set, kept so the next recommendation can use them and so the
owner can always see what Vyterlix thinks it knows.
"""

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.integrations.base import utcnow
from app.memory import rules
from app.models.actions import BusinessIntervention
from app.models.data import Product, Sale, SaleLine
from app.models.kpi import KpiDefinition, KpiValue
from app.models.memory import (
    BusinessLearning,
    BusinessMemory,
    InterventionPattern,
    MemoryRetrievalEvent,
)
from app.models.outcomes import InterventionOutcome
from app.models.recommendations import Intervention
from app.schemas.memory import (
    ConstraintsIn,
    ConstraintsOut,
    LessonOut,
    MemoryItemOut,
    MemoryOut,
    PatternOut,
    RebuildOut,
    RetrievalOut,
)
from app.services import track_record
from app.services.goals import list_goals
from app.services.seasons import list_seasons

logger = logging.getLogger("vyterlix.memory")

DERIVED_KINDS = ("normal_range", "customer_pattern", "goal", "season")
RECENT_USE = 15


def fmt(value: Decimal, unit: str) -> str:
    """A figure in its own unit."""
    if unit == "gbp":
        return f"£{value:,.2f}"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:.2f}"


# --- writing one fact ----------------------------------------------------------------------------


def _put(db: Session, tenant, kind, key, title, statement, data, source) -> BusinessMemory:
    row = db.scalars(
        select(BusinessMemory).where(BusinessMemory.kind == kind, BusinessMemory.key == key)
    ).first()
    if row is None:
        row = BusinessMemory(organization_id=tenant.organization_id, kind=kind, key=key)
        db.add(row)
    row.title, row.statement, row.data, row.source = title, statement, data, source
    row.updated_by_user_id = tenant.user.id if source == "owner" else None
    row.updated_at = utcnow()
    db.flush()
    return row


# --- what is normal, patterns, goals, seasons (worked out from the business's own records) -------


def _normal_ranges(db: Session, tenant) -> dict[str, tuple]:
    found = {}
    for kpi in db.scalars(select(KpiDefinition).where(KpiDefinition.is_active.is_(True))):
        rows = db.execute(
            select(KpiValue.period_start, KpiValue.value)
            .where(
                KpiValue.kpi_id == kpi.id,
                KpiValue.granularity == "month",
                KpiValue.status == "ok",
                KpiValue.is_complete.is_(True),
                KpiValue.value.is_not(None),
            )
            .order_by(KpiValue.period_start)
        ).all()
        band = rules.normal_range([Decimal(v) for _, v in rows], count=kpi.unit == "count")
        if band is None or round(band.low, 2) == round(band.high, 2):
            continue  # too little to go on, or it never moves
        latest_month, latest = rows[-1][0], Decimal(rows[-1][1])
        where = rules.position(latest, band)
        statement = (
            f"{kpi.name} is usually between {fmt(band.low, kpi.unit)} and {fmt(band.high, kpi.unit)} "
            f"a month (it averaged {fmt(band.mean, kpi.unit)} over the last {band.months} months). "
            f"The latest month, {latest_month:%B %Y}, was {fmt(latest, kpi.unit)}, "
            + {
                "below": "below what is usual.",
                "above": "above what is usual.",
                "within": "within what is usual.",
            }[where]
        )
        data = {
            "kpi_code": kpi.code, "unit": kpi.unit, "months": band.months, "mean": str(round(band.mean, 2)),
            "low": str(round(band.low, 2)), "high": str(round(band.high, 2)),
            "latest": str(round(latest, 2)), "latest_month": latest_month.isoformat(), "position": where,
        }  # fmt: skip
        found[f"range:{kpi.code}"] = (f"What is normal for {kpi.name}", statement, data)
    return found


def _customer_patterns(db: Session) -> dict[str, tuple]:
    found = {}
    sales = (Sale.kind == "sale",)
    weekday = extract("isodow", Sale.sold_on)
    per_day = {
        int(day) - 1: Decimal(total)
        for day, total in db.execute(
            select(weekday, func.sum(Sale.net_amount)).where(*sales).group_by(weekday)
        )
    }
    days = rules.busiest_days(per_day)
    if days is not None:
        best, worst, best_share, worst_share = days
        found["weekdays"] = (
            "Your busiest and quietest days",
            f"{rules.WEEKDAYS[best]} is your busiest day ({best_share:.0f}% of sales) and "
            f"{rules.WEEKDAYS[worst]} your quietest ({worst_share:.0f}%).",
            {"busiest": rules.WEEKDAYS[best], "quietest": rules.WEEKDAYS[worst],
             "busiest_share": str(round(best_share, 1)), "quietest_share": str(round(worst_share, 1))},
        )  # fmt: skip
    visits = dict(
        db.execute(
            select(Sale.customer_id, func.count())
            .where(*sales, Sale.customer_id.is_not(None))
            .group_by(Sale.customer_id)
        ).all()
    )
    if visits:
        repeaters = sum(1 for n in visits.values() if n >= 2)
        pct = repeaters / len(visits) * 100
        found["repeat_customers"] = (
            "How many customers come back",
            f"{repeaters} of your {len(visits)} named customers ({pct:.0f}%) have bought more than once.",
            {
                "customers": len(visits),
                "repeat": repeaters,
                "repeat_pct": str(round(Decimal(pct), 1)),
            },
        )
    lines = db.execute(
        select(Product.name, func.sum(SaleLine.net_amount))
        .join(Product, Product.id == SaleLine.product_id)
        .join(Sale, Sale.id == SaleLine.sale_id)
        .where(*sales)
        .group_by(Product.name)
        .order_by(func.sum(SaleLine.net_amount).desc())
    ).all()
    total = sum((Decimal(t) for _, t in lines), Decimal(0))
    if len(lines) >= 3 and total > 0:
        top = lines[:3]
        part = sum((Decimal(t) for _, t in top), Decimal(0))
        share = part / total * 100
        names = ", ".join(n for n, _ in top)
        found["top_products"] = (
            "What you rely on most",
            f"Your three best sellers ({names}) bring in {share:.0f}% of your sales.",
            {"products": [n for n, _ in top], "share_pct": str(round(share, 1))},
        )
    return found


def _goals(db: Session) -> dict[str, tuple]:
    found = {}
    for g in list_goals(db, "active"):
        target = ""
        if g.target_value is not None:
            target = f", aiming for {fmt(Decimal(g.target_value), g.target_unit or 'count')}" + (
                f" by {g.target_date:%d/%m/%Y}" if g.target_date else ""
            )
        found[f"goal:{g.id}"] = (
            g.title, f"Goal (priority {g.priority} of 5): {g.title}{target}.",
            {"goal_id": str(g.id), "kpi_code": g.kpi_code, "priority": g.priority},
        )  # fmt: skip
    return found


def _seasons(db: Session) -> dict[str, tuple]:
    found = {}
    for s in list_seasons(db, "active"):
        change = (
            ""
            if s.expected_change_pct is None
            else f", usually {abs(s.expected_change_pct):.0f}% {'busier' if s.expected_change_pct >= 0 else 'quieter'}"
        )
        found[f"season:{s.id}"] = (
            s.name, f"{s.name}: {s.label}{change}.",
            {"season_id": str(s.id), "expected_change_pct": None if s.expected_change_pct is None else str(s.expected_change_pct)},
        )  # fmt: skip
    return found


def rebuild(db: Session, tenant) -> RebuildOut:
    """Work out again what is normal and what the patterns are, from the records as they are now.
    Facts that no longer hold are removed. The owner's own limits are never touched."""
    made = {
        "normal_range": _normal_ranges(db, tenant),
        "customer_pattern": _customer_patterns(db),
        "goal": _goals(db),
        "season": _seasons(db),
    }
    removed = 0
    for kind, items in made.items():
        for key, (title, statement, data) in items.items():
            _put(db, tenant, kind, key, title, statement, data, "derived")
        for row in db.scalars(
            select(BusinessMemory).where(
                BusinessMemory.kind == kind, BusinessMemory.source == "derived"
            )
        ).all():
            if row.key not in items:
                db.delete(row)
                removed += 1
    db.commit()
    return RebuildOut(
        normal_ranges=len(made["normal_range"]), customer_patterns=len(made["customer_pattern"]),
        goals=len(made["goal"]), seasons=len(made["season"]), removed=removed,
    )  # fmt: skip


# --- the owner's limits --------------------------------------------------------------------------


def constraints(db: Session) -> rules.Constraints:
    rows = {
        r.key: r
        for r in db.scalars(
            select(BusinessMemory).where(BusinessMemory.kind.in_(("constraint", "preference")))
        )
    }

    def value(key, default=None):
        return rows[key].data.get("value", default) if key in rows else default

    return rules.Constraints(
        max_cost_level=value("max_cost_level"),
        max_effort=value("max_effort"),
        excluded_actions=set(value("excluded_actions", [])),
        quick_results_only=bool(value("quick_results_only", False)),
    )


def constraints_out(db: Session) -> ConstraintsOut:
    c = constraints(db)
    names = {i.code: i.name for i in db.scalars(select(Intervention))}
    updated = db.scalar(
        select(func.max(BusinessMemory.updated_at)).where(
            BusinessMemory.kind.in_(("constraint", "preference"))
        )
    )
    return ConstraintsOut(
        max_cost_level=c.max_cost_level, max_effort=c.max_effort,
        excluded_actions=sorted(c.excluded_actions),
        excluded_names=[names.get(code, code) for code in sorted(c.excluded_actions)],
        quick_results_only=c.quick_results_only, updated_at=updated,
    )  # fmt: skip


def set_constraints(db: Session, tenant, body: ConstraintsIn) -> ConstraintsOut:
    """Replace the owner's limits. An unknown action code is refused."""
    known = {i.code for i in db.scalars(select(Intervention))}
    unknown = sorted(set(body.excluded_actions) - known)
    if unknown:
        raise AppError(
            f"We do not know the action {unknown[0]!r}.", code="unknown_action", status_code=422
        )
    words = {"none": "nothing", "low": "a little", "medium": "a moderate amount", "high": "a lot"}
    efforts = {
        "low": "a little effort",
        "medium": "a moderate amount of effort",
        "high": "a lot of effort",
    }
    _put(db, tenant, "constraint", "max_cost_level", "Most you can spend on an action",
         "No limit on cost." if body.max_cost_level is None else f"An action may cost {words[body.max_cost_level]}, no more.",
         {"value": body.max_cost_level}, "owner")  # fmt: skip
    _put(db, tenant, "constraint", "max_effort", "Most effort you can give",
         "No limit on effort." if body.max_effort is None else f"An action may take {efforts[body.max_effort]}, no more.",
         {"value": body.max_effort}, "owner")  # fmt: skip
    names = {i.code: i.name for i in db.scalars(select(Intervention))}
    left = sorted(set(body.excluded_actions))
    _put(db, tenant, "constraint", "excluded_actions", "Actions never to suggest",
         "No action is ruled out." if not left else "Never suggest: " + ", ".join(names[c] for c in left) + ".",
         {"value": left}, "owner")  # fmt: skip
    _put(db, tenant, "preference", "quick_results_only", "Only quick results",
         "Only suggest actions that show within a month." if body.quick_results_only else "Slower actions are fine.",
         {"value": body.quick_results_only}, "owner")  # fmt: skip
    db.commit()
    return constraints_out(db)


# --- learning from results -----------------------------------------------------------------------


def learn(db: Session, tenant, intervention_id: uuid.UUID) -> None:
    """Keep what was learned from one measured result: a lesson in words, and the count for that
    kind of action on that figure. Safe to call again: a result is only learned from once."""
    if db.scalars(
        select(BusinessLearning).where(BusinessLearning.intervention_id == intervention_id)
    ).first():
        return
    intervention = db.get(BusinessIntervention, intervention_id)
    outcome = db.scalars(
        select(InterventionOutcome).where(InterventionOutcome.intervention_id == intervention_id)
    ).first()
    if intervention is None or outcome is None:
        return
    kpi = db.get(KpiDefinition, intervention.kpi_id)
    code = intervention.library_code
    db.add(
        BusinessLearning(
            organization_id=tenant.organization_id, intervention_id=intervention_id, library_code=code,
            kpi_code=kpi.code, outcome=outcome.outcome, achieved_pct=outcome.achieved_pct,
            lesson=rules.lesson(
                action=intervention.title, kpi_name=kpi.name, outcome=outcome.outcome,
                achieved_pct=outcome.achieved_pct, reason=outcome.reason,
            ),
        )
    )  # fmt: skip
    db.flush()
    if code is not None:
        _count_up(db, tenant, code, kpi.code)
    db.commit()


def _count_up(db: Session, tenant, code: str, kpi_code: str) -> None:
    """Count the lessons for one kind of action on one figure afresh (so the pattern is always just
    what the lessons say, however many times this runs)."""
    lessons = db.scalars(
        select(BusinessLearning).where(
            BusinessLearning.library_code == code, BusinessLearning.kpi_code == kpi_code
        )
    ).all()
    pattern = db.scalars(
        select(InterventionPattern).where(
            InterventionPattern.library_code == code, InterventionPattern.kpi_code == kpi_code
        )
    ).first()
    if pattern is None:
        pattern = InterventionPattern(
            organization_id=tenant.organization_id, library_code=code, kpi_code=kpi_code
        )
        db.add(pattern)
    for outcome in ("successful", "partially_successful", "unsuccessful", "inconclusive"):
        setattr(pattern, outcome, sum(1 for x in lessons if x.outcome == outcome))
    pattern.average_achieved_pct = rules.average_achieved(
        [x.achieved_pct for x in lessons if x.outcome != "inconclusive"]
    )
    latest = max(lessons, key=lambda x: x.created_at)
    outcome = db.scalars(
        select(InterventionOutcome).where(
            InterventionOutcome.intervention_id == latest.intervention_id
        )
    ).first()
    pattern.last_outcome = latest.outcome
    pattern.last_measured_at = None if outcome is None else outcome.measured_at


# --- using memory to make a recommendation ----------------------------------------------------------


@dataclass
class Recall:
    constraints: rules.Constraints
    records: dict[str, track_record.Record] = field(
        default_factory=dict
    )  # for this figure, by action
    cases: list[rules.Case] = field(default_factory=list)

    def history(self, code: str, general: dict[str, track_record.Record]) -> int | None:
        """The track record score for an action on this figure: what has happened on this very figure
        if it has been tried here, otherwise what has happened anywhere in the business, else None."""
        here = self.records.get(code)
        if here is not None and here.decided:
            return here.score
        elsewhere = general.get(code)
        return None if elsewhere is None else elsewhere.score


def recall(db: Session, kpi_code: str) -> Recall:
    patterns = db.scalars(
        select(InterventionPattern).where(InterventionPattern.kpi_code == kpi_code)
    ).all()
    records = {
        p.library_code: track_record.Record(
            p.successful, p.partially_successful, p.unsuccessful, p.inconclusive
        )
        for p in patterns
    }
    cases = [
        rules.Case(row.library_code, row.kpi_code, row.outcome, row.achieved_pct, row.lesson)
        for row in db.scalars(select(BusinessLearning).where(BusinessLearning.kpi_code == kpi_code))
    ]
    return Recall(constraints(db), records, rules.similar_cases(cases, kpi_code))


def log_use(db: Session, tenant, event_id: uuid.UUID | None, used: list[dict]) -> None:
    """Write down what memory was used for a recommendation, so it can be shown later."""
    if used:
        db.add(
            MemoryRetrievalEvent(
                organization_id=tenant.organization_id, purpose="recommendation", event_id=event_id,
                used=used, created_at=utcnow(),
            )
        )  # fmt: skip


# --- reading ---------------------------------------------------------------------------------------


def _item(r: BusinessMemory) -> MemoryItemOut:
    return MemoryItemOut(
        kind=r.kind,
        key=r.key,
        title=r.title,
        statement=r.statement,
        data=r.data,
        source=r.source,
        updated_at=r.updated_at,
    )


def _of_kind(db: Session, kind: str) -> list[MemoryItemOut]:
    return [
        _item(r)
        for r in db.scalars(
            select(BusinessMemory)
            .where(BusinessMemory.kind == kind)
            .order_by(BusinessMemory.title, BusinessMemory.key)
        )
    ]


def read(db: Session) -> MemoryOut:
    names = {i.code: i.name for i in db.scalars(select(Intervention))}
    kpis = {k.code: k.name for k in db.scalars(select(KpiDefinition))}
    lessons = [
        LessonOut(
            action=names.get(row.library_code), kpi_code=row.kpi_code,
            kpi_name=kpis.get(row.kpi_code, row.kpi_code), outcome=row.outcome,
            achieved_pct=row.achieved_pct, lesson=row.lesson, learned_at=row.created_at,
        )
        for row in db.scalars(select(BusinessLearning).order_by(BusinessLearning.created_at.desc()))
    ]  # fmt: skip
    patterns = [
        PatternOut(
            action=names.get(p.library_code, p.library_code), code=p.library_code,
            kpi_code=p.kpi_code, kpi_name=kpis.get(p.kpi_code, p.kpi_code),
            successful=p.successful, partially_successful=p.partially_successful,
            unsuccessful=p.unsuccessful, inconclusive=p.inconclusive,
            average_achieved_pct=None if p.average_achieved_pct is None else str(round(p.average_achieved_pct)),
            last_outcome=p.last_outcome,
        )
        for p in db.scalars(select(InterventionPattern).order_by(InterventionPattern.kpi_code, InterventionPattern.library_code))
    ]  # fmt: skip
    recent = [
        RetrievalOut(
            id=e.id, purpose=e.purpose, event_id=e.event_id, used=e.used, created_at=e.created_at
        )
        for e in db.scalars(
            select(MemoryRetrievalEvent)
            .order_by(MemoryRetrievalEvent.created_at.desc())
            .limit(RECENT_USE)
        )
    ]
    return MemoryOut(
        normal_ranges=_of_kind(db, "normal_range"), customer_patterns=_of_kind(db, "customer_pattern"),
        goals=_of_kind(db, "goal"), seasons=_of_kind(db, "season"), constraints=constraints_out(db),
        lessons=lessons, patterns=patterns, recent_use=recent,
        last_rebuilt=db.scalar(select(func.max(BusinessMemory.updated_at)).where(BusinessMemory.source == "derived")),
    )  # fmt: skip
