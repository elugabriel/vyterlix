# ruff: noqa: E501
"""What Vyterlix remembers about a business, and how that is used.

Pure rules, no database (services/memory.py applies them).

Three kinds of memory feed the next recommendation:
- the owner's limits (how much an action may cost, how much effort, actions never to suggest, and
  whether only quick results are wanted): an action that breaks one is left out, and the
  recommendation says so;
- how each kind of action has worked before on the same figure: that replaces the neutral track
  record for it (the more specific record wins over the general one);
- similar cases: what happened last time this figure fell and something was tried.
"""

import statistics
from dataclasses import dataclass, field
from decimal import Decimal

RULES_VERSION = "memory-1"
MIN_MONTHS_FOR_RANGE = 6
RANGE_MONTHS = 12
QUICK_DAYS = 30  # "quick results only" means showing within a month
COST_ORDER = ("none", "low", "medium", "high")
EFFORT_ORDER = ("low", "medium", "high")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_CASES = 3


@dataclass
class NormalRange:
    months: int
    mean: Decimal
    low: Decimal  # one standard deviation below the mean
    high: Decimal


def normal_range(values: list[Decimal], *, count: bool = False) -> NormalRange | None:
    """What is usual for a figure: its average over the last year and the band a typical month falls
    in (one standard deviation either side). Needs half a year of figures to mean anything."""
    recent = values[-RANGE_MONTHS:]
    if len(recent) < MIN_MONTHS_FOR_RANGE:
        return None
    mean = statistics.fmean(float(v) for v in recent)
    spread = statistics.pstdev(float(v) for v in recent)
    low = max(0.0, mean - spread) if count else mean - spread  # a count cannot be below nothing
    return NormalRange(
        len(recent), Decimal(str(mean)), Decimal(str(low)), Decimal(str(mean + spread))
    )


def position(value: Decimal, band: NormalRange) -> str:
    """Where a figure sits against what is usual."""
    if value < band.low:
        return "below"
    if value > band.high:
        return "above"
    return "within"


@dataclass
class Constraints:
    max_cost_level: str | None = None
    max_effort: str | None = None
    excluded_actions: set[str] = field(default_factory=set)
    quick_results_only: bool = False

    @property
    def any(self) -> bool:
        return bool(
            self.max_cost_level
            or self.max_effort
            or self.excluded_actions
            or self.quick_results_only
        )


def broken_limit(
    constraints: Constraints, *, code: str, effort: str, cost_level: str, days: int
) -> str | None:
    """The first of the owner's limits an action breaks, in words; None if it breaks none."""
    if code in constraints.excluded_actions:
        return "you asked us not to suggest this kind of action"
    cost_cap = constraints.max_cost_level
    if cost_cap and COST_ORDER.index(cost_level) > COST_ORDER.index(cost_cap):
        return f"it costs more than you said you can spend ({cost_level} cost, your limit is {cost_cap})"
    effort_cap = constraints.max_effort
    if effort_cap and EFFORT_ORDER.index(effort) > EFFORT_ORDER.index(effort_cap):
        return f"it takes more effort than you said you can give ({effort} effort, your limit is {effort_cap})"
    if constraints.quick_results_only and days > QUICK_DAYS:
        return f"you only want quick results and it takes about {days} days to show"
    return None


def busiest_days(totals: dict[int, Decimal]) -> tuple[int, int, Decimal, Decimal] | None:
    """(busiest weekday, quietest weekday, busiest share %, quietest share %) from sales per weekday
    (0 = Monday), or None with too little to say."""
    total = sum(totals.values(), Decimal(0))
    open_days = {d: v for d, v in totals.items() if v > 0}
    if total <= 0 or len(open_days) < 2:
        return None
    best = max(open_days, key=open_days.get)
    worst = min(open_days, key=open_days.get)
    return best, worst, open_days[best] / total * 100, open_days[worst] / total * 100


def share(part: Decimal, whole: Decimal) -> Decimal | None:
    return None if whole <= 0 else part / whole * 100


def lesson(
    *, action: str, kpi_name: str, outcome: str, achieved_pct: int | None, reason: str
) -> str:
    """What was learned from one result, in a sentence."""
    if outcome == "successful":
        return f'"{action}" worked for {kpi_name}: {reason}'
    if outcome == "partially_successful":
        return f'"{action}" helped {kpi_name} only in part: {reason}'
    if outcome == "unsuccessful":
        return f'"{action}" did not work for {kpi_name}: {reason}'
    return f'We could not tell whether "{action}" worked for {kpi_name}: {reason}'


def average_achieved(values: list[int | None]) -> Decimal | None:
    """The average share of the expected change that was achieved, over the results that have one."""
    known = [v for v in values if v is not None]
    return None if not known else Decimal(sum(known)) / len(known)


@dataclass
class Case:
    """One earlier time something was tried on the same figure."""

    code: str | None
    kpi_code: str
    outcome: str
    achieved_pct: int | None
    lesson: str


def similar_cases(cases: list[Case], kpi_code: str) -> list[Case]:
    """The earlier cases on the same figure that told us something, the ones that worked first, then
    the ones that did not (inconclusive ones say nothing), the strongest result in each group first."""
    rank = {"successful": 0, "unsuccessful": 1, "partially_successful": 2}
    found = [c for c in cases if c.kpi_code == kpi_code and c.outcome in rank]
    found.sort(key=lambda c: (rank[c.outcome], -(c.achieved_pct or 0)))
    return found[:MAX_CASES]
