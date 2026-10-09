# ruff: noqa: E501
"""Writing each kind of report: gathering results other parts of the system already worked out and
laying them out as sections. Nothing is calculated here that is not already stored, and nothing is
guessed: where there is nothing to say, the report says so.

Everyone in a business can read its figures, so a report has the same content whoever it is written for.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.actions import BusinessAction, BusinessIntervention
from app.models.alerts import Alert
from app.models.identity import User
from app.models.kpi import KpiDefinition, KpiValue
from app.models.memory import BusinessLearning
from app.models.outcomes import InterventionOutcome
from app.reports import rules
from app.services import detection, health, kpi, outcomes
from app.services.memory import fmt

CATEGORY_LABEL = {
    "financial": "Money", "sales": "Sales", "customer": "Customers", "inventory": "Stock",
    "marketing": "Marketing", "operational": "Operations",
}  # fmt: skip
STATUS_LABEL = {
    "healthy": "Healthy", "fair": "Fair", "needs_attention": "Needs attention",
    "at_risk": "At risk", "not_enough_data": "Not enough data",
}  # fmt: skip
HEADLINE = ("revenue", "gross_profit", "net_profit", "active_customers", "average_order_value")
MAX_ROWS = 12
NOTE_SOURCE = "Every figure comes from the records you gave Vyterlix. Amounts described as estimates are starting estimates, not promises."
NO_FIGURES = (
    "There are no finished months of figures yet. Bring in your sales, costs and customers first."
)


def latest_month(db: Session) -> date | None:
    return db.scalar(
        select(func.max(KpiValue.period_start)).where(
            KpiValue.granularity == "month", KpiValue.is_complete.is_(True), KpiValue.status == "ok"
        )
    )


def _money(value, unit: str) -> str:
    return "-" if value is None else fmt(Decimal(str(value)), unit)


def _delta(unit: str, now, before) -> str:
    """How a figure moved: points for a percentage, per cent for everything else."""
    if now is None or before is None:
        return "-"
    now, before = Decimal(str(now)), Decimal(str(before))
    if unit == "percent":
        d = now - before
        return f"{'+' if d > 0 else ''}{d:.1f} points"
    if before == 0:
        return "-"
    d = (now - before) / before * 100
    return f"{'+' if d > 0 else ''}{d:.1f}%"


def _series(db: Session, code: str, months: list[date]) -> dict[date, KpiValue | None]:
    history = kpi.kpi_history(db, code, "month", 24)
    got = {
        v.period_start: v
        for v in history.values
        if v.is_complete and v.status == "ok" and v.value is not None
    }
    return {m: got.get(m) for m in months}


# --- health ------------------------------------------------------------------------------------------------


def health_sections(db: Session, months: list[date]) -> list[dict]:
    h = health.latest_health(db)
    if h is None or h.overall_score is None:
        return [
            rules.section(
                "Business health",
                paragraphs=[
                    "Business health has not been worked out yet. It needs your sales and costs first."
                ],
            )
        ]
    out = []
    change = "" if h.previous_score is None else f" It was {h.previous_score} the month before."
    out.append(
        rules.section(
            "Where you stand",
            paragraphs=[
                f"Your business health for {h.period_start:%B %Y} is {h.overall_score} out of 100 ({STATUS_LABEL.get(h.status, h.status).lower()}).{change}",
                h.explanation,
            ],
        )
    )
    rows = [
        [
            c.label,
            "-" if c.score is None else c.score,
            STATUS_LABEL.get(c.status, c.status),
            "-"
            if c.previous_score is None or c.score is None
            else f"{c.score - c.previous_score:+d}",
            f"{c.weight * 100:.0f}%" if c.weight <= 1 else f"{c.weight:.0f}%",
        ]
        for c in h.components
    ]
    out.append(
        rules.section(
            "The areas of the business",
            columns=["Area", "Score (out of 100)", "Status", "Change", "How much it counts"],
            rows=rows,
        )
    )
    points = [
        p
        for p in health.health_history(db, 24).points
        if p.period_start in set(months) and p.overall_score is not None
    ]
    if points:
        out.append(
            rules.section(
                "How it has moved",
                columns=["Month", "Score (out of 100)", "Status", "Coverage"],
                rows=[
                    [
                        f"{p.period_start:%B %Y}",
                        p.overall_score,
                        STATUS_LABEL.get(p.status, p.status),
                        f"{p.coverage_pct}%",
                    ]
                    for p in points
                ],
            )
        )
    metrics = sorted((m for c in h.components for m in c.metrics), key=lambda m: m.score)[:5]
    if metrics:
        out.append(rules.section("What is holding it back", bullets=[m.text for m in metrics]))
    return out


# --- key figures ---------------------------------------------------------------------------------------------


def kpi_sections(db: Session, months: list[date]) -> list[dict]:
    definitions = kpi.active_definitions(db)
    columns = ["Figure", *[f"{m:%b %y}" for m in months], "Latest change"]
    by_category: dict[str, list[list]] = {}
    for d in definitions:
        series = _series(db, d.code, months)
        if not any(series.values()):
            continue
        values = [series[m].value if series[m] else None for m in months]
        previous = next((series[m].previous_value for m in reversed(months) if series[m]), None)
        latest = next((v for v in reversed(values) if v is not None), None)
        by_category.setdefault(d.category, []).append(
            [d.name, *[_money(v, d.unit) for v in values], _delta(d.unit, latest, previous)]
        )
    if not by_category:
        return [rules.section("Key figures", paragraphs=[NO_FIGURES])]
    return [
        rules.section(CATEGORY_LABEL.get(cat, cat.title()), columns=columns, rows=rows)
        for cat, rows in by_category.items()
    ]


# --- what you tried -------------------------------------------------------------------------------------------


def _owner_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i is not None}
    return (
        {i: n for i, n in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))}
        if ids
        else {}
    )


def outcomes_sections(db: Session, first: date, last: date) -> list[dict]:
    out = []
    summary = outcomes.summary(db)
    checked = (
        summary.successful
        + summary.partially_successful
        + summary.unsuccessful
        + summary.inconclusive
    )
    if not checked and not summary.waiting:
        return [
            rules.section(
                "What you tried and how it went",
                paragraphs=["Nothing you have tried has been finished and checked yet."],
            )
        ]
    sentence = f"Of the things you tried that have been checked: {summary.successful} worked, {summary.partially_successful} partly worked, {summary.unsuccessful} did not work and {summary.inconclusive} could not be judged."
    if summary.waiting:
        sentence += f" {summary.waiting} finished and are waiting to be checked."
    out.append(rules.section("How it has gone", paragraphs=[sentence]))
    rows = db.execute(
        select(BusinessAction, BusinessIntervention, InterventionOutcome, KpiDefinition)
        .join(BusinessIntervention, BusinessIntervention.id == BusinessAction.intervention_id)
        .join(KpiDefinition, KpiDefinition.id == BusinessIntervention.kpi_id)
        .outerjoin(
            InterventionOutcome, InterventionOutcome.intervention_id == BusinessIntervention.id
        )
        .where(
            BusinessIntervention.accepted_at >= _start(first),
            BusinessIntervention.accepted_at < _start(rules.shift(last.replace(day=1), 1)),
        )
        .order_by(BusinessIntervention.accepted_at)
    ).all()
    names = _owner_names(db, {a.owner_user_id for a, *_ in rows})
    if rows:
        out.append(
            rules.section(
                "What you took up in this period",
                columns=["Action", "Figure", "Accepted", "Who", "State", "Result"],
                rows=[
                    [
                        a.title,
                        k.name,
                        f"{i.accepted_at:%d/%m/%Y}",
                        names.get(a.owner_user_id, "Nobody"),
                        _state(a),
                        o.outcome.replace("_", " ") if o else "not yet",
                    ]
                    for a, i, o, k in rows
                ],
            )
        )
    measured = db.execute(
        select(InterventionOutcome, BusinessIntervention, KpiDefinition)
        .join(BusinessIntervention, BusinessIntervention.id == InterventionOutcome.intervention_id)
        .join(KpiDefinition, KpiDefinition.id == BusinessIntervention.kpi_id)
        .where(
            InterventionOutcome.measured_at >= _start(first),
            InterventionOutcome.measured_at < _start(rules.shift(last.replace(day=1), 1)),
        )
        .order_by(InterventionOutcome.measured_at)
    ).all()
    if measured:
        out.append(
            rules.section(
                "Results measured in this period",
                columns=["Action", "Figure", "Expected", "What happened", "Result"],
                rows=[
                    [
                        i.title,
                        k.name,
                        _money(o.expected_change, k.unit),
                        _money(
                            o.adjusted_change if o.adjusted_change is not None else o.actual_change,
                            k.unit,
                        ),
                        rules_label(o.outcome),
                    ]
                    for o, i, k in measured
                ],
                bullets=[o.reason for o, *_ in measured][:MAX_ROWS],
            )
        )
    lessons = db.scalars(
        select(BusinessLearning)
        .where(
            BusinessLearning.created_at >= _start(first),
            BusinessLearning.created_at < _start(rules.shift(last.replace(day=1), 1)),
        )
        .order_by(BusinessLearning.created_at)
    ).all()
    if lessons:
        out.append(
            rules.section("What was learned", bullets=[x.lesson for x in lessons][:MAX_ROWS])
        )
    if summary.track_record:
        out.append(
            rules.section(
                "How each kind of action has worked for you",
                columns=[
                    "Kind of action",
                    "Worked",
                    "Partly",
                    "Did not",
                    "Could not tell",
                    "Score (out of 100)",
                ],
                rows=[
                    [
                        t.name,
                        t.successful,
                        t.partially_successful,
                        t.unsuccessful,
                        t.inconclusive,
                        t.score,
                    ]
                    for t in summary.track_record
                ],
            )
        )
    return out


def rules_label(outcome: str) -> str:
    from app.outcomes.rules import OUTCOME_LABELS

    return OUTCOME_LABELS.get(outcome, outcome)


def _start(day: date):
    from datetime import UTC, datetime

    return datetime.combine(day, datetime.min.time(), tzinfo=rules.UK_TZ).astimezone(UTC)


def _state(a: BusinessAction) -> str:
    return {
        "pending": "Waiting for approval",
        "accepted": "Accepted",
        "in_progress": "In progress",
        "partially_completed": "Partly done",
        "completed": "Done",
        "cancelled": "Cancelled",
        "overdue": "Overdue",
    }[a.status]


# --- the monthly report ----------------------------------------------------------------------------------------------


def monthly_sections(db: Session, month: date) -> list[dict]:
    out = []
    h = health.latest_health(db)
    open_alerts = (
        db.scalar(select(func.count()).select_from(Alert).where(Alert.status != "resolved")) or 0
    )
    lines = []
    if h is not None and h.overall_score is not None:
        lines.append(
            f"Your business health for {h.period_start:%B %Y} is {h.overall_score} out of 100 ({STATUS_LABEL.get(h.status, h.status).lower()})."
        )
    lines.append(
        f"{open_alerts} {'alert is' if open_alerts == 1 else 'alerts are'} open."
        if open_alerts
        else "No alerts are open."
    )
    out.append(rules.section("At a glance", paragraphs=lines))
    previous = rules.shift(month, -1)
    rows = []
    for code in HEADLINE:
        d = next((x for x in kpi.active_definitions(db) if x.code == code), None)
        if d is None:
            continue
        s = _series(db, code, [previous, month])
        if s[month] is None:
            continue
        rows.append(
            [
                d.name,
                _money(s[month].value, d.unit),
                _money(s[previous].value if s[previous] else None, d.unit),
                _delta(d.unit, s[month].value, s[previous].value if s[previous] else None),
            ]
        )
    if rows:
        out.append(
            rules.section(
                "Your key figures",
                columns=["Figure", month.strftime("%B %Y"), previous.strftime("%B %Y"), "Change"],
                rows=rows,
            )
        )
    else:
        out.append(rules.section("Your key figures", paragraphs=[NO_FIGURES]))
    events = detection.list_events(db, month=month, include_expected=False, limit=MAX_ROWS)
    if events:
        out.append(
            rules.section(
                f"What changed in {month:%B %Y}",
                columns=["What", "Good or bad", "Size"],
                rows=[
                    [
                        e.summary,
                        {"good": "Good news", "bad": "Worth a look", "neutral": "A change"}[
                            e.effect
                        ],
                        e.severity.replace("notable", "Noticeable").replace("major", "Big"),
                    ]
                    for e in events
                ],
            )
        )
    else:
        out.append(
            rules.section(
                f"What changed in {month:%B %Y}", paragraphs=["Nothing moved by more than usual."]
            )
        )
    open_actions = (
        db.execute(
            select(BusinessAction)
            .where(
                BusinessAction.status.in_(
                    ("pending", "accepted", "in_progress", "partially_completed", "overdue")
                )
            )
            .order_by(BusinessAction.target_date.asc().nulls_last())
        )
        .scalars()
        .all()
    )
    names = _owner_names(db, {a.owner_user_id for a in open_actions})
    if open_actions:
        out.append(
            rules.section(
                "Your actions",
                paragraphs=[
                    f"{len(open_actions)} still to do, {sum(1 for a in open_actions if a.status == 'overdue')} of them overdue."
                ],
                columns=["Action", "State", "Finish by", "Who"],
                rows=[
                    [
                        a.title,
                        _state(a),
                        f"{a.target_date:%d/%m/%Y}" if a.target_date else "No date",
                        names.get(a.owner_user_id, "Nobody"),
                    ]
                    for a in open_actions[:MAX_ROWS]
                ],
            )
        )
    else:
        out.append(rules.section("Your actions", paragraphs=["You have no actions still to do."]))
    results = [
        s
        for s in outcomes_sections(db, month, month)
        if s["heading"] in ("Results measured in this period", "What was learned")
    ]
    for s in results:
        s["heading"] = (
            "What you tried: results" if s["heading"].startswith("Results") else "What was learned"
        )
        out.append(s)
    alerts = db.scalars(select(Alert).where(Alert.status != "resolved")).all()
    alerts.sort(
        key=lambda a: (-["info", "low", "medium", "high", "critical"].index(a.severity), a.title)
    )
    if alerts:
        out.append(
            rules.section(
                "Open alerts",
                columns=["How serious", "What"],
                rows=[[a.severity.title(), a.title] for a in alerts[:MAX_ROWS]],
            )
        )
    return out


# --- putting a report together ------------------------------------------------------------------------------------------


def build(db: Session, *, kind: str, title: str, business: str, months: int, now) -> dict:
    """The stored content of one report."""
    latest = latest_month(db)
    generated = now.astimezone(rules.UK_TZ)
    facts = [f"Written for {business} on {generated:%d/%m/%Y}."]
    if latest is None:
        first = last = generated.date().replace(day=1)
        sections = [rules.section(title, paragraphs=[NO_FIGURES])]
        facts.insert(0, "No finished months of figures yet.")
    else:
        window = rules.months_window(latest, 1 if kind == "monthly" else months)
        first, last = window[0], rules.last_day(window[-1])
        facts.insert(0, f"Covers {rules.period_label(window[0], window[-1])}.")
        sections = {
            "monthly": lambda: monthly_sections(db, latest),
            "health": lambda: health_sections(db, window),
            "kpi": lambda: kpi_sections(db, window),
            "outcomes": lambda: outcomes_sections(db, window[0], window[-1]),
        }[kind]()
    return {
        "title": title, "business": business, "facts": facts, "period_start": first.isoformat(),
        "period_end": last.isoformat(), "generated_at": now.isoformat(), "sections": sections,
        "notes": [NOTE_SOURCE, f"Written with report rules {rules.RULES_VERSION}."], "kind": kind,
    }  # fmt: skip
