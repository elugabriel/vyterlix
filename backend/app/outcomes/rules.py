"""Did an action work? The rules for choosing the month to look at and judging the result.

Pure rules, no database (services/outcomes.py applies them).

An action that has been finished is checked once, after it has had time to show. The figure it was
meant to move is read for a full calendar month that began after the work finished, and compared
with the figure when the action was accepted. What it was expected to win back is the starting
estimate made at the time. Where last year's figures exist, the change that month normally brings
at that time of year is taken out first, so a seasonal rise is not mistaken for the action working.

- successful: at least 80% of what was expected
- partially successful: at least 30% of it
- unsuccessful: less than that (including a figure that got worse)
- inconclusive: we cannot tell: no figure for the month, the data for it is too incomplete, no
  expected figure to compare with, or the time of year alone would have brought the expected change
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

RULES_VERSION = "outcomes-1"
SUCCESS_AT = Decimal(80)  # per cent of the expected change
PARTIAL_AT = Decimal(30)
MIN_QUALITY = 60  # data quality (out of 100) below which a month is not trusted
SEASON_EXPLAINS_AT = Decimal(80)  # per cent of the expected change the season alone brings
GRACE_DAYS = 60  # how long to wait for the month's figures before calling it inconclusive
PERCENT_LIMITS = (-100, 999)

OUTCOME_LABELS = {
    "successful": "It worked",
    "partially_successful": "It partly worked",
    "unsuccessful": "It did not work",
    "inconclusive": "We cannot tell",
}


def _first_of_next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def first_full_month(completed_on: date) -> date:
    """The first calendar month that began after the work was finished."""
    return _first_of_next_month(completed_on)


def follow_up_due(completed_on: date, days_to_effect: int) -> date:
    """When to check: once it has had its usual time to show and a full month has gone by."""
    shown_by = completed_on + timedelta(days=days_to_effect)
    return max(shown_by, _first_of_next_month(first_full_month(completed_on)))


def month_to_measure(due: date) -> date:
    """The latest month that had finished by the due date."""
    return date(due.year - (due.month == 1), (due.month - 2) % 12 + 1, 1)


def a_year_earlier(month: date) -> date:
    return date(month.year - 1, month.month, 1)


def improvement(direction: str, before: Decimal, after: Decimal) -> Decimal:
    """The change, counted as positive when the business is better off."""
    return after - before if direction == "up_good" else before - after


def history_score(successful: int, partial: int, unsuccessful: int) -> int:
    """Track record out of 100. With nothing recorded it is 50, and each result moves it a little:
    a success counts as one, a partial success as half, and the start counts as half a success."""
    decided = successful + partial + unsuccessful
    return round(
        (Decimal(successful) + Decimal("0.5") * partial + Decimal("0.5")) / (decided + 1) * 100
    )


@dataclass
class Verdict:
    outcome: str
    reason: str
    achieved_pct: int | None = None
    adjusted: Decimal | None = None


def judge(
    *,
    expected: Decimal | None,
    actual: Decimal,
    seasonal: Decimal | None,
    data_quality: int | None,
    show: Callable[[Decimal], str],
) -> Verdict:
    """The verdict on one action. `expected`, `actual` and `seasonal` are all counted as positive
    when the business is better off; `show` writes an amount in the figure's own unit."""
    if expected is None or expected <= 0:
        return Verdict("inconclusive", "There was no expected result to compare with.")
    if data_quality is not None and data_quality < MIN_QUALITY:
        return Verdict(
            "inconclusive",
            f"The figures for that month are too incomplete to trust ({data_quality} out of 100).",
        )
    adjusted = actual - (seasonal or Decimal(0))
    pct = adjusted / expected * 100
    achieved = max(PERCENT_LIMITS[0], min(PERCENT_LIMITS[1], round(pct)))
    if (
        seasonal is not None
        and seasonal / expected * 100 >= SEASON_EXPLAINS_AT
        and pct < SUCCESS_AT
    ):
        return Verdict(
            "inconclusive",
            f"This time of year normally brings a change of {show(seasonal)} on its own, about as "
            f"much as the {show(expected)} the action was expected to win back, so we cannot tell "
            "what the action did.",
            achieved,
            adjusted,
        )
    took_out = (
        f" after taking out {show(seasonal)} that the time of year brings" if seasonal else ""
    )
    expected_text = f"against the {show(expected)} we expected"
    if pct >= SUCCESS_AT:
        outcome = "successful"
        reason = f"It won back {show(adjusted)}{took_out}, {expected_text} ({achieved}%)."
    elif pct >= PARTIAL_AT:
        outcome = "partially_successful"
        reason = f"It won back only {show(adjusted)}{took_out}, {expected_text} ({achieved}%)."
    else:
        outcome = "unsuccessful"
        reason = (
            f"It won back only {show(adjusted)}{took_out}, {expected_text} ({achieved}%)."
            if adjusted > 0
            else f"It did not win anything back: the figure moved by {show(adjusted)}{took_out}, "
            f"{expected_text}."
        )
    return Verdict(outcome, reason, achieved, adjusted)
