"""When is a change big enough to matter? Pure rules, no database.

A figure that moves a little every month is normal, so a change only counts once it is large
for the kind of figure it is:

- A figure measured in pounds, a count or a ratio is judged by how many per cent it moved.
  15% is notable and 30% is major. A tiny starting point (under £100, under 5 items) makes any
  percentage meaningless, so those are left alone.
- A figure that is already a percentage (a profit margin of 12%) is judged by how many
  percentage points it moved: 3 is notable and 8 is major. "Up 50%" on a 2% margin would be
  misleading.

These are Vyterlix's own starting values, kept here in one place so they are easy to tune.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.health.scoring import format_value

HUNDRED = Decimal("100")
NOTABLE_PERCENT = Decimal("15")
MAJOR_PERCENT = Decimal("30")
NOTABLE_POINTS = Decimal("3")
MAJOR_POINTS = Decimal("8")
# Below this starting level a percentage change says nothing.
MIN_BASE = {"gbp": Decimal("100"), "count": Decimal("5"), "ratio": Decimal("0.1")}
# A change is "what the season leads you to expect" when the season accounts for at least this
# share of it, in the same direction.
SEASON_SHARE = Decimal("2") / Decimal("3")


def measure_change(unit: str, value: Decimal, previous: Decimal) -> tuple[Decimal, str] | None:
    """(signed change, 'percent' or 'points'), or None when the starting point is too small."""
    value, previous = Decimal(value), Decimal(previous)
    if unit == "percent":
        return value - previous, "points"
    if abs(previous) < MIN_BASE.get(unit, Decimal("0")) or previous == 0:
        return None
    return (value - previous) / abs(previous) * HUNDRED, "percent"


def severity_for(change: Decimal, change_unit: str) -> str | None:
    """'major', 'notable', or None when the change is within the ordinary."""
    notable, major = (
        (NOTABLE_POINTS, MAJOR_POINTS)
        if change_unit == "points"
        else (NOTABLE_PERCENT, MAJOR_PERCENT)
    )
    size = abs(Decimal(change))
    if size >= major:
        return "major"
    return "notable" if size >= notable else None


def effect_for(direction: str, change: Decimal) -> str:
    """Is it good or bad news? Depends on which way is better for this figure."""
    if direction == "neutral" or change == 0:
        return "neutral"
    went_up = change > 0
    return "good" if went_up == (direction == "up_good") else "bad"


def expected_season_change(effect_now: Decimal, effect_before: Decimal) -> Decimal | None:
    """The change, in per cent, that the seasons alone lead you to expect between two months.

    Each effect is how much busier (+) or quieter (-) than normal that month is, in per cent.
    None when a season says -100% or worse (nothing to compare with).
    """
    now, before = 1 + Decimal(effect_now) / HUNDRED, 1 + Decimal(effect_before) / HUNDRED
    if now <= 0 or before <= 0:
        return None
    return (now / before - 1) * HUNDRED


def season_explains(change: Decimal, expected: Decimal | None) -> bool:
    """Do the seasons account for most of the change, in the same direction?"""
    if expected is None or expected == 0 or change == 0 or (change > 0) != (expected > 0):
        return False
    return abs(expected) >= abs(Decimal(change)) * SEASON_SHARE


def describe(
    name: str,
    unit: str,
    month: str,
    before: str,
    value: Decimal,
    previous: Decimal,
    change: Decimal,
    change_unit: str,
    *,
    explained_by_season: bool = False,
) -> str:
    """One plain sentence: what moved, by how much, and from what to what."""
    verb = "rose" if change > 0 else "fell"
    size = abs(Decimal(change))
    how = f"{size:.1f} points" if change_unit == "points" else f"{size:.0f}%"
    came = "up" if change > 0 else "down"
    text = (
        f"{name} {verb} by {how} in {month}: {format_value(value, unit)}, "
        f"{came} from {format_value(previous, unit)} in {before}."
    )
    if explained_by_season:
        text += " That is about what your busy and quiet seasons would lead you to expect."
    return text


# --- an unusual month, judged against the business's own history -------------------------------
#
# Comparing with last month misses a slow slide, and flags a normal bounce back. So each figure is
# also compared with its own recent history: the middle value of up to the last 12 finished
# months, and how far this figure normally strays from it. It is an anomaly only when it is both
# far outside its normal range AND a big enough change to matter (the same size bands as above),
# so a very steady figure that wobbles by half a per cent is not reported.

HISTORY_MONTHS = 12  # how far back "usual" looks
MIN_HISTORY = 6  # fewer months than this and we do not know what is usual yet
UNUSUAL_Z = Decimal("3")  # this many "normal wobbles" from the usual
MAD_TO_SPREAD = Decimal("1.4826")  # turns the median absolute deviation into a standard spread
MIN_SPREAD_SHARE = Decimal("0.02")  # a spread is never taken to be less than 2% of the usual...
MIN_SPREAD_POINTS = Decimal("0.5")  # ...or half a percentage point for a figure that is a %


@dataclass
class Anomaly:
    usual: Decimal  # the middle of the history
    spread: Decimal  # how far the figure normally strays from it
    score: Decimal  # how many spreads away this month is (signed)
    change: Decimal  # signed distance from usual, in per cent or points
    change_unit: str
    severity: str


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(Decimal(v) for v in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def usual_and_spread(unit: str, history: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    """(usual, spread) from the earlier months, or None when there are too few of them."""
    if len(history) < MIN_HISTORY:
        return None
    usual = _median(history)
    spread = _median([abs(Decimal(v) - usual) for v in history]) * MAD_TO_SPREAD
    floor = MIN_SPREAD_POINTS if unit == "percent" else abs(usual) * MIN_SPREAD_SHARE
    return usual, max(spread, floor)


def find_anomaly(unit: str, value: Decimal, history: list[Decimal]) -> Anomaly | None:
    """Is this month's value far outside what is normal for the figure, and by enough to matter?"""
    basis = usual_and_spread(unit, history)
    if basis is None:
        return None
    usual, spread = basis
    if spread == 0:
        return None
    score = (Decimal(value) - usual) / spread
    if abs(score) < UNUSUAL_Z:
        return None
    measured = measure_change(unit, Decimal(value), usual)
    if measured is None:
        return None
    change, change_unit = measured
    severity = severity_for(change, change_unit)
    if severity is None:
        return None
    return Anomaly(usual, spread, score, change, change_unit, severity)


def describe_anomaly(
    name: str,
    unit: str,
    month: str,
    value: Decimal,
    usual: Decimal,
    change: Decimal,
    change_unit: str,
    months: int,
) -> str:
    """One plain sentence: how this month compares with the usual, and that it stands out."""
    size = abs(Decimal(change))
    how = f"{size:.1f} points" if change_unit == "points" else f"{size:.0f}%"
    side = "above" if change > 0 else "below"
    return (
        f"{name} in {month} was {format_value(value, unit)}, {how} {side} your usual "
        f"{format_value(usual, unit)} (the middle of the last {months} months). That is further "
        "from normal than this figure usually strays."
    )
