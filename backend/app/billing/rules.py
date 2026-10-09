# ruff: noqa: E501
"""What a subscription allows, and when: the rules of billing.

Pure rules, no database and no payment provider (services/billing.py applies them).

A business with no paid subscription is on a free trial that gives the top self-serve plan for a number
of days. When the trial (or a payment) runs out, the few gated features switch off, but nothing a
business has already put in is ever held back: its figures, reports and downloads stay readable.
Prices exclude VAT; UK VAT is added to what is charged.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("0.20")
TRIAL_PLAN = "scale"  # the plan a trial gives
GRACE_DAYS = 7  # a payment that failed keeps the plan this long after the period ended
FEATURE_NOUN = {
    "members": "team members",
    "integrations": "connections to other systems",
    "scheduled_reports": "scheduled reports",
    "ai_assistant": "AI assistant",
}
FEATURE_LABEL = {
    "members": "Team members",
    "integrations": "Connections",
    "scheduled_reports": "Scheduled reports",
    "ai_assistant": "AI assistant",
}
ENTITLED = ("trialing", "active", "past_due")


# --- money --------------------------------------------------------------------------------------------


def vat(net_pence: int) -> int:
    """The VAT on an amount, to the nearest penny (half a penny rounds up)."""
    return int((Decimal(net_pence) * VAT_RATE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def money(pence: int | None, currency: str = "GBP") -> str:
    if pence is None:
        return "-"
    sign = "-" if pence < 0 else ""
    amount = Decimal(abs(pence)) / 100
    if currency == "GBP":
        return f"{sign}£{amount:,.2f}"
    return f"{sign}{currency} {amount:,.2f}"


def price(plan, interval: str) -> int | None:
    """The price of a plan for a month or a year, excluding VAT (none when it cannot be bought)."""
    return plan.price_year_pence if interval == "year" else plan.price_month_pence


def year_saving(plan) -> int | None:
    """What paying for a year at once saves compared with twelve months."""
    if plan.price_month_pence is None or plan.price_year_pence is None:
        return None
    return plan.price_month_pence * 12 - plan.price_year_pence


# --- time -----------------------------------------------------------------------------------------------


def add_months(moment: datetime, months: int) -> datetime:
    """The same time of day a number of months later (the 31st becomes the last day of a short month)."""
    index = moment.year * 12 + moment.month - 1 + months
    year, month = index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def period_end(start: datetime, interval: str) -> datetime:
    return add_months(start, 12 if interval == "year" else 1)


def trial_days_left(trial_ends_at: datetime | None, now: datetime) -> int:
    """Whole days left, counting a part of a day as a day (0 once it has ended)."""
    if trial_ends_at is None or trial_ends_at <= now:
        return 0
    return math.ceil((trial_ends_at - now).total_seconds() / 86400)


def from_unix(value: int | float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, tz=UTC)


# --- what state a subscription is really in ----------------------------------------------------------------


def effective_status(
    status: str,
    *,
    trial_ends_at: datetime | None,
    period_end_at: datetime | None,
    cancel_at_period_end: bool,
    now: datetime,
) -> str:
    """The state a subscription is in right now, whatever the last message from the provider said."""
    if status == "trialing":
        return "expired" if trial_ends_at is None or now >= trial_ends_at else "trialing"
    if status == "active":
        if cancel_at_period_end and period_end_at is not None and now >= period_end_at:
            return "canceled"
        return "active"
    if status == "past_due":
        if period_end_at is not None and now >= period_end_at + timedelta(days=GRACE_DAYS):
            return "expired"
        return "past_due"
    return status  # canceled, expired


def entitled(effective: str) -> bool:
    return effective in ENTITLED


# --- what a plan allows -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Allowance:
    enabled: bool
    limit: int | None  # none: no limit


BLOCKED = Allowance(False, 0)


def allowance(row, effective: str) -> Allowance:
    """What a plan allows for one feature (a row of feature_entitlements), now."""
    if row is None or not entitled(effective) or not row.enabled:
        return BLOCKED
    return Allowance(True, row.limit)


@dataclass(frozen=True)
class Decision:
    ok: bool
    message: str = ""


def decide(feature: str, allow: Allowance, used: int, plan_name: str, effective: str) -> Decision:
    """Whether one more of a feature may be added, and if not, why, in words."""
    if not entitled(effective):
        return Decision(
            False, "Your plan has ended. Choose a plan to carry on adding to your business."
        )
    if not allow.enabled:
        return Decision(
            False,
            f"The {FEATURE_NOUN[feature]} is not part of the {plan_name} plan. Upgrade to use it.",
        )
    if allow.limit is not None and used >= allow.limit:
        return Decision(
            False,
            f"The {plan_name} plan includes up to {allow.limit} {FEATURE_NOUN[feature]} and you already have {used}. Upgrade to add more.",
        )
    return Decision(True)


def downgrade_problems(target_rows: dict, usage: dict[str, int], target_name: str) -> list[str]:
    """What stops a business moving to a smaller plan: what it has now that the smaller plan cannot
    hold. (A feature the smaller plan leaves out has nothing counted, so it never stops a move.)"""
    problems = []
    for feature, noun in FEATURE_NOUN.items():
        row = target_rows.get(feature)
        if row is None or not row.enabled or row.limit is None:
            continue
        used = usage.get(feature, 0)
        if used > row.limit:
            problems.append(
                f"You have {used} {noun} and the {target_name} plan allows {row.limit}."
            )
    return problems


def is_upgrade(current, target) -> bool:
    """Moving to a dearer plan (by the monthly price; a plan that cannot be priced counts as dearest)."""
    now = current.price_month_pence if current.price_month_pence is not None else math.inf
    then = target.price_month_pence if target.price_month_pence is not None else math.inf
    return then > now
