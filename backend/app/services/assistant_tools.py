# ruff: noqa: E501
"""What the assistant can look up. Every answer is made from what these return and nothing else.

Each tool reads the business's own results from the part of the system that worked them out (the KPI
engine, business health, detected changes and their explanations, forecasts, recommendations, actions,
outcomes and memory) and writes down what it found as short plain-English facts with the figures in
them. A tool never calculates a figure of its own that is not already stored, never guesses, and says
so when there is nothing to report. What is shown depends on who is asking: only people who can act on
an area see the steps and names of the work in it.
"""

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.answers import ToolResult
from app.ai.intents import Understood
from app.core.errors import AppError, NotFoundError
from app.core.permissions import KpiCategory, Perm
from app.models.kpi import KpiDefinition, KpiValue
from app.services import actions as actions_service
from app.services import (
    detection,
    diagnosis,
    forecast,
    health,
    kpi,
    memory,
    outcomes,
    recommendations,
)
from app.services.memory import fmt

logger = logging.getLogger("vyterlix.assistant")

NEEDS_A_FIGURE = ("kpi_value", "trend", "why", "forecast", "normal")
WHICH_FIGURE = "Which figure do you mean? For example: sales, profit, customers, refunds or stock."
MAX_EVIDENCE = 5
MAX_ACTIONS = 5
TREND_MONTHS = 6
EVIDENCE_LEAD = {
    "fact": "From your records: ",
    "statistical": "From your figures: ",
    "insufficient": "We cannot tell: ",
}  # a statement the assistant itself would have to interpret is never passed on


def _month(day) -> str:
    return f"{day:%B %Y}"


def _date(day) -> str:
    return f"{day:%d/%m/%Y}"


def latest_month(db: Session):
    """The latest finished month there are figures for."""
    return db.scalar(
        select(func.max(KpiValue.period_start)).where(
            KpiValue.granularity == "month", KpiValue.is_complete.is_(True), KpiValue.status == "ok"
        )
    )


def figure_words(db: Session) -> dict[str, tuple[str, ...]]:
    """Every figure's own name, as one more way of asking for it."""
    return {k.code: (k.name.lower(),) for k in kpi.active_definitions(db)}


def _definition(db: Session, code: str) -> KpiDefinition | None:
    return next((d for d in kpi.active_definitions(db) if d.code == code), None)


def _cannot(tool: str, note: str, **arguments) -> ToolResult:
    return ToolResult(tool, False, note=note, arguments=arguments)


# --- one tool each --------------------------------------------------------------------------------------


def health_tool(db: Session, tenant, u: Understood) -> ToolResult:
    h = health.latest_health(db)
    if h is None:
        return _cannot(
            "health",
            "Your business health has not been worked out yet. It needs your sales and costs first.",
        )
    scored = [c for c in h.components if c.score is not None]
    facts = [
        f"Your business health for {_month(h.period_start)} is {h.overall_score} out of 100 ({h.status.replace('_', ' ')})."
    ]
    if h.previous_score is not None:
        facts.append(f"It was {h.previous_score} the month before.")
    if scored:
        weakest, strongest = min(scored, key=lambda c: c.score), max(scored, key=lambda c: c.score)
        facts.append(f"The weakest area is {weakest.label} ({weakest.score} out of 100).")
        if strongest.category != weakest.category:
            facts.append(f"The strongest is {strongest.label} ({strongest.score} out of 100).")
    facts.append(f"{h.coverage_pct}% of the picture could be scored.")
    return ToolResult(
        "health", True, facts,
        [{"kind": "health", "label": f"Business health, {_month(h.period_start)}", "ref": h.period_start.isoformat(), "link": "health.html"}],
        context={"intent": "health"},
    )  # fmt: skip


def kpi_tool(db: Session, tenant, u: Understood) -> ToolResult:
    history = kpi.kpi_history(db, u.kpi_code, "month", 24)
    finished = [v for v in history.values if v.is_complete and v.status == "ok"]
    if u.month is not None:
        row = next((v for v in finished if v.period_start == u.month), None)
        if row is None:
            return _cannot(
                "kpi",
                f"I have no figure for {history.name} in {_month(u.month)}.",
                kpi=u.kpi_code,
                month=u.month.isoformat(),
            )
    elif finished:
        row = finished[-1]
    else:
        return _cannot("kpi", f"I have no finished month of {history.name} yet.", kpi=u.kpi_code)
    unit = history.unit
    facts = [f"{history.name} in {_month(row.period_start)} was {fmt(Decimal(row.value), unit)}."]
    if row.previous_value is not None:
        before = f"{fmt(Decimal(row.previous_value), unit)} the month before"
        if row.change_pct is not None and unit != "percent":
            facts.append(
                f"That is {abs(Decimal(row.change_pct)):.1f}% {'up' if Decimal(row.change_pct) >= 0 else 'down'} on {before}."
            )
        else:
            facts.append(f"It was {before}.")
    if row.data_quality is not None and row.data_quality < 80:
        facts.append(
            f"Some of the data behind this month is incomplete ({row.data_quality} out of 100), so treat it with care."
        )
    return ToolResult(
        "kpi", True, facts,
        [{"kind": "kpi", "label": f"{history.name}, {_month(row.period_start)}", "ref": f"{u.kpi_code}:{row.period_start.isoformat()}", "link": f"kpis.html#{u.kpi_code}"}],
        context={"intent": "kpi_value", "kpi_code": u.kpi_code, "month": row.period_start.isoformat()},
        arguments={"kpi": u.kpi_code, "month": row.period_start.isoformat()},
    )  # fmt: skip


def trend_tool(db: Session, tenant, u: Understood) -> ToolResult:
    history = kpi.kpi_history(db, u.kpi_code, "month", 24)
    rows = [
        v for v in history.values if v.is_complete and v.status == "ok" and v.value is not None
    ][-TREND_MONTHS:]
    if len(rows) < 2:
        return _cannot(
            "trend",
            f"There are not enough months of {history.name} to show a trend yet.",
            kpi=u.kpi_code,
        )
    unit = history.unit
    values = [Decimal(v.value) for v in rows]
    listing = ", ".join(f"{v.period_start:%b %Y} {fmt(Decimal(v.value), unit)}" for v in rows)
    facts = [f"{history.name} over the last {len(rows)} months: {listing}."]
    first, last = values[0], values[-1]
    if unit == "percent":
        facts.append(
            f"From {rows[0].period_start:%B %Y} to {rows[-1].period_start:%B %Y} it moved by {abs(last - first):.1f} points {'up' if last >= first else 'down'}."
        )
    elif first != 0:
        facts.append(
            f"From {rows[0].period_start:%B %Y} to {rows[-1].period_start:%B %Y} it is {abs((last - first) / first * 100):.1f}% {'up' if last >= first else 'down'}."
        )
    high, low = max(rows, key=lambda v: Decimal(v.value)), min(rows, key=lambda v: Decimal(v.value))
    facts.append(
        f"The highest was {fmt(Decimal(high.value), unit)} in {_month(high.period_start)} and the lowest {fmt(Decimal(low.value), unit)} in {_month(low.period_start)}."
    )
    return ToolResult(
        "trend", True, facts,
        [{"kind": "kpi", "label": f"{history.name}, last {len(rows)} months", "ref": u.kpi_code, "link": f"kpis.html#{u.kpi_code}"}],
        context={"intent": "trend", "kpi_code": u.kpi_code}, arguments={"kpi": u.kpi_code},
    )  # fmt: skip


def _event_for(db: Session, u: Understood, bad_only: bool):
    events = detection.list_events(db, effect="bad" if bad_only else None, limit=200)
    if u.kpi_code:
        events = [e for e in events if e.kpi_code == u.kpi_code]
    if u.month:
        events = [e for e in events if e.period_start == u.month]
    return events[0] if events else None  # newest month first, biggest first within it


def why_tool(db: Session, tenant, u: Understood) -> ToolResult:
    definition = _definition(db, u.kpi_code)
    name = definition.name if definition else u.kpi_code
    event = _event_for(db, u, bad_only=False)
    if event is None:
        when = f" in {_month(u.month)}" if u.month else ""
        return _cannot(
            "why",
            f"I have not seen an unusual change in {name}{when}, so there is nothing to explain.",
            kpi=u.kpi_code,
        )
    try:
        d = diagnosis.read(db, event.id)
    except NotFoundError:
        category = KpiCategory(event.category)
        if not tenant.can(Perm.ACTIONS_MANAGE, category):
            return _cannot(
                "why",
                f"{event.summary} It has not been explained yet. The owner or a manager can ask for an explanation on the What changed page.",
                event=str(event.id),
            )
        d = diagnosis.diagnose(db, tenant, event.id)
    facts = [event.summary, d.summary]  # the summary already opens with the headline
    if d.confidence is not None:
        facts.append(f"How sure we are: {d.confidence_label} ({d.confidence} out of 100).")
    shown = [e for e in d.evidence if e.evidence_type in EVIDENCE_LEAD][:MAX_EVIDENCE]
    facts += [EVIDENCE_LEAD[e.evidence_type] + e.statement for e in shown]
    return ToolResult(
        "why", True, [f for f in facts if f],
        [{"kind": "change", "label": f"{event.kpi_name}, {_month(event.period_start)}", "ref": str(event.id), "link": "changes.html"}],
        context={"intent": "why", "kpi_code": event.kpi_code, "month": event.period_start.isoformat(), "event_id": str(event.id)},
        arguments={"event": str(event.id)},
    )  # fmt: skip


def forecast_tool(db: Session, tenant, u: Understood) -> ToolResult:
    try:
        f = forecast.read_latest(db, u.kpi_code)
    except AppError:
        f = None  # a figure that is never forecast
    if f is None:
        figures = forecast.forecastable_figures(db)
        if any(x.code == u.kpi_code for x in figures):
            return _cannot(
                "forecast",
                "A forecast for that figure has not been worked out yet. It needs about half a year of figures.",
                kpi=u.kpi_code,
            )
        names = ", ".join(x.name for x in figures)
        return _cannot(
            "forecast", f"I do not forecast that figure. I forecast: {names}.", kpi=u.kpi_code
        )
    if f.status != "ok" or not f.predictions:
        return _cannot(
            "forecast",
            f"There is not enough history of {f.kpi_name} to forecast it yet. About half a year of figures is needed.",
            kpi=u.kpi_code,
        )
    facts = [
        f"{f.kpi_name} is expected to be {fmt(Decimal(p.value), f.unit)} in {_month(p.period_start)}, most likely between {fmt(Decimal(p.lower), f.unit)} and {fmt(Decimal(p.upper), f.unit)}."
        for p in f.predictions[:3]
    ]
    facts.append(f"The range is meant to hold {f.interval_level}% of the time. {f.explanation}")
    return ToolResult(
        "forecast", True, facts,
        [{"kind": "forecast", "label": f"{f.kpi_name} forecast", "ref": u.kpi_code, "link": "forecast.html"}],
        context={"intent": "forecast", "kpi_code": u.kpi_code}, arguments={"kpi": u.kpi_code},
    )  # fmt: skip


def recommend_tool(db: Session, tenant, u: Understood) -> ToolResult:
    event = _event_for(db, u, bad_only=True)
    if event is None:
        return _cannot(
            "recommend",
            "I have not seen a fall that needs putting right, so there is nothing to suggest. Ask about a figure that has dropped, or look at What changed.",
            kpi=u.kpi_code,
        )
    try:
        r = recommendations.read(db, event.id)
    except NotFoundError:
        return _cannot(
            "recommend",
            f"{event.summary} Nothing has been suggested for it yet. The owner or a manager can ask for suggestions on the What changed page.",
            event=str(event.id),
        )
    if not r.options:
        return ToolResult(
            "recommend",
            True,
            [r.headline, r.rationale],
            [
                {
                    "kind": "recommendation",
                    "label": r.headline,
                    "ref": str(event.id),
                    "link": "changes.html",
                }
            ],
            context={"intent": "recommend", "kpi_code": event.kpi_code, "event_id": str(event.id)},
        )
    best = r.options[0]
    can_act = tenant.can(Perm.ACTIONS_MANAGE, KpiCategory(event.category))
    facts = [
        r.headline,
        f"{best.description}",
        f"It could win back about {fmt(Decimal(best.impact_value), best.impact_unit)} (a starting estimate, not a promise). It takes {best.effort} effort and {best.cost_level} cost, and should start to show in about {best.days_to_effect} days.",
        r.rationale,
    ]
    if can_act:
        facts.append("How to do it: " + "; ".join(best.intervention.steps) + ".")
    else:
        facts.append("To take this up, ask the owner or a manager.")
    return ToolResult(
        "recommend", True, facts,
        [{"kind": "recommendation", "label": r.headline, "ref": str(event.id), "link": "changes.html"}],
        context={"intent": "recommend", "kpi_code": event.kpi_code, "month": event.period_start.isoformat(), "event_id": str(event.id)},
        arguments={"event": str(event.id)},
    )  # fmt: skip


def actions_tool(db: Session, tenant, u: Understood) -> ToolResult:
    counts = actions_service.counts(db, tenant)
    if not (counts.open or counts.completed or counts.pending or counts.cancelled):
        return _cannot(
            "actions",
            "You have not taken up any actions yet. You can accept a suggestion on the What changed page.",
        )
    facts = [
        f"You have {counts.open} actions still to do, {counts.overdue} overdue and {counts.due_soon} due within a week; {counts.completed} are done."
    ]
    if counts.pending:
        facts.append(f"{counts.pending} are waiting for the owner to approve.")
    show_people = tenant.has(Perm.ACTIONS_MANAGE)
    for a in actions_service.list_actions(db, tenant, open_only=True)[:MAX_ACTIONS]:
        line = f"{a.title}: {a.status_label.lower()}"
        if a.target_date:
            line += f", finish by {_date(a.target_date)}"
        if a.days_late:
            line += f" ({a.days_late} days late)"
        if show_people and a.owner:
            line += f", with {a.owner.name}"
        facts.append(line + ".")
    return ToolResult(
        "actions",
        True,
        facts,
        [{"kind": "actions", "label": "Your actions", "ref": "actions", "link": "actions.html"}],
        context={"intent": "actions"},
    )


def outcomes_tool(db: Session, tenant, u: Understood) -> ToolResult:
    s = outcomes.summary(db)
    checked = s.successful + s.partially_successful + s.unsuccessful + s.inconclusive
    if not checked and not s.waiting:
        return _cannot("outcomes", "Nothing you have tried has been finished and checked yet.")
    facts = []
    if checked:
        facts.append(
            f"Of the things you tried that have been checked: {s.successful} worked, {s.partially_successful} partly worked, {s.unsuccessful} did not work and {s.inconclusive} could not be judged."
        )
    if s.waiting:
        facts.append(f"{s.waiting} finished and are waiting to be checked.")
    facts += [lesson.lesson for lesson in memory.read(db).lessons[:3]]
    return ToolResult(
        "outcomes",
        True,
        facts,
        [
            {
                "kind": "outcomes",
                "label": "What you tried and how it went",
                "ref": "outcomes",
                "link": "actions.html",
            }
        ],
        context={"intent": "outcomes"},
    )


def normal_tool(db: Session, tenant, u: Understood) -> ToolResult:
    row = memory.read(db)
    item = next((i for i in row.normal_ranges if i.key == f"range:{u.kpi_code}"), None)
    if item is None:
        return _cannot(
            "normal",
            "There are not enough months of that figure yet to say what is normal for you. About half a year is needed.",
            kpi=u.kpi_code,
        )
    return ToolResult(
        "normal", True, [item.statement],
        [{"kind": "memory", "label": item.title, "ref": item.key, "link": "memory.html"}],
        context={"intent": "normal", "kpi_code": u.kpi_code}, arguments={"kpi": u.kpi_code},
    )  # fmt: skip


TOOLS = {
    "health": health_tool, "kpi_value": kpi_tool, "trend": trend_tool, "why": why_tool,
    "forecast": forecast_tool, "recommend": recommend_tool, "actions": actions_tool,
    "outcomes": outcomes_tool, "normal": normal_tool,
}  # fmt: skip


def run(db: Session, tenant, u: Understood) -> list[ToolResult]:
    """Look up what the question needs. Nothing is looked up for a question that was not understood."""
    tool = TOOLS.get(u.intent)
    if tool is None:
        return []
    if u.intent in NEEDS_A_FIGURE and u.kpi_code is None:
        return [_cannot("clarify", WHICH_FIGURE)]
    try:
        return [tool(db, tenant, u)]
    except NotFoundError as exc:
        return [_cannot(u.intent, str(exc))]
