"""How good were the forecasts? Pure arithmetic on forecasts that can now be checked against what
really happened (a month that was forecast has since finished).

Two questions matter and they are different:

- how far off was the forecast, on average (the typical miss, in pounds and per cent), and does it
  lean one way (always too high, or too low)?
- did the real figure land inside the range we gave? A range promised to hold 80 times out of 100
  that only holds 50 times is too narrow, however good the middle figure is.

Needs at least three checked months before it will pass judgement: one or two are luck.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from app.health.scoring import format_value

MIN_CHECKED = 3
BIAS_QUIET = 2.0  # a lean of under 2% either way counts as about right
SLACK = 10.0  # how far under the promised share still counts as the ranges doing their job


@dataclass
class Checked:
    """One forecast month whose real figure is now known."""

    predicted: float
    lower: float
    upper: float
    actual: float


@dataclass
class Accuracy:
    n: int
    within: int
    mae: float
    rmse: float
    mape: float | None  # per cent; None if every actual was zero
    bias_pct: float | None  # forecast minus actual, as a per cent of actual; + means too high


def within_range(item: Checked) -> bool:
    return item.lower <= item.actual <= item.upper


def score(items: list[Checked]) -> Accuracy | None:
    """The typical miss, how often the range held, and which way the forecasts lean."""
    if not items:
        return None
    errors = [i.actual - i.predicted for i in items]
    relative = [(i.predicted - i.actual) / abs(i.actual) * 100 for i in items if i.actual != 0]
    absolute = [abs(r) for r in relative]
    return Accuracy(
        n=len(items),
        within=sum(within_range(i) for i in items),
        mae=sum(abs(e) for e in errors) / len(errors),
        rmse=math.sqrt(sum(e * e for e in errors) / len(errors)),
        mape=None if not absolute else sum(absolute) / len(absolute),
        bias_pct=None if not relative else sum(relative) / len(relative),
    )


def bias_text(bias_pct: float | None) -> str:
    if bias_pct is None or abs(bias_pct) < BIAS_QUIET:
        return "about right on average, not leaning either way"
    side = "high" if bias_pct > 0 else "low"
    return f"{abs(bias_pct):.0f}% too {side} on average"


def verdict(share_within: float, promised: int) -> str:
    """Are the ranges as trustworthy as they claim to be?"""
    if share_within >= promised - SLACK:
        return "The ranges are doing their job."
    return (
        "The ranges are too narrow: real results land outside them more often than we promised, "
        "so treat the forecast with more care than the range suggests."
    )


def headline(
    result: Accuracy | None, unit: str, promised: int, name: str
) -> tuple[str, str | None]:
    """(a plain summary, a verdict on the ranges or None when it is too early to say)."""
    if result is None or result.n < MIN_CHECKED:
        have = 0 if result is None else result.n
        return (
            f"It is too early to judge how accurate the {name} forecasts are: only {have} "
            f"forecast month{'s' if have != 1 else ''} {'have' if have != 1 else 'has'} finished "
            f"so far, and we need {MIN_CHECKED}.",
            None,
        )
    share = result.within / result.n * 100
    miss = format_value(Decimal(str(result.mae)), unit)
    if result.mape is not None:
        miss += f" ({result.mape:.0f}%)"
    text = (
        f"Of {result.n} forecast months checked, {result.within} ({share:.0f}%) landed inside the "
        f"range we gave (we aim for {promised}%). On average the forecast was off by {miss}, "
        f"and was {bias_text(result.bias_pct)}."
    )
    return text, verdict(share, promised)
