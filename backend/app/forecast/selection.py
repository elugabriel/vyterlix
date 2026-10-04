"""Choosing a method and saying how far to trust it.

Each method is tried on the business's own recent history, the way it would really be used: cover
up the last few months, forecast the next one from what came before, and compare with what
happened (a "backtest"). The method that missed by the least wins, and the size of its misses
sets the range around its forecasts: if it is usually off by £800, a forecast of £10,000 comes
with a range of a few hundred pounds either side, not a promise.

Needs at least six months of history (three to learn from and three to test on).
"""

import math
from dataclasses import dataclass

from app.forecast.models import METHODS, Method

MIN_HISTORY = 6
MIN_LEARN = 3  # months a method always has to learn from before the first test
MAX_TEST = 6  # how many of the latest months to test on
INTERVAL_LEVEL = 80  # the real figure should land inside the range this often
# z values for the usual ranges (a normal spread of misses)
Z = {50: 0.6745, 60: 0.8416, 70: 1.0364, 80: 1.2816, 90: 1.6449, 95: 1.96, 99: 2.5758}
MIN_SPREAD_SHARE = 0.02  # a range is never narrower than 2% of the level, however lucky the test


@dataclass
class Score:
    code: str
    mae: float  # the typical size of the miss, in the figure's own unit
    rmse: float
    mape: float | None  # the typical miss in per cent (None if the figure was zero on test months)
    n_points: int
    errors: list[float]  # actual - forecast, one per test month


def test_months(n: int) -> int:
    """How many of the latest months every method is tried on."""
    return min(MAX_TEST, n - MIN_LEARN)


def eligible(history: list[float]) -> list[Method]:
    """The methods that can be tested on this history: they need enough months before the first
    test month."""
    if len(history) < MIN_HISTORY:
        return []
    start = len(history) - test_months(len(history))
    return [m for m in METHODS.values() if start >= m.min_history]


def backtest(
    method: Method,
    history: list[float],
    non_negative: bool = True,
    ceiling: float | None = None,
) -> Score:
    """Forecast each of the latest months one step ahead from what came before it."""
    n = len(history)
    errors = []
    for t in range(n - test_months(n), n):
        guess = method.predict(history[:t], 1)[0]
        if non_negative:
            guess = max(0.0, guess)
        if ceiling is not None:
            guess = min(ceiling, guess)
        errors.append(history[t] - guess)
    actuals = history[n - len(errors) :]
    relative = [abs(e) / abs(a) for e, a in zip(errors, actuals, strict=True) if a != 0]
    return Score(
        method.code,
        sum(abs(e) for e in errors) / len(errors),
        math.sqrt(sum(e * e for e in errors) / len(errors)),
        None if not relative else sum(relative) / len(relative) * 100,
        len(errors),
        errors,
    )


def choose(scores: list[Score]) -> Score | None:
    """The method with the smallest typical miss; if equal, the simplest (first in METHODS)."""
    if not scores:
        return None
    order = list(METHODS)
    return min(scores, key=lambda s: (round(s.mae, 9), order.index(s.code)))


def spread(score: Score, history: list[float]) -> float:
    """How far real figures usually stray from this method's forecast (one step ahead)."""
    level = abs(sum(history[-3:]) / len(history[-3:]))
    return max(score.rmse, level * MIN_SPREAD_SHARE)


def interval(
    point: float,
    sigma: float,
    step: int,
    level: int = INTERVAL_LEVEL,
    non_negative: bool = True,
    ceiling: float | None = None,
) -> tuple[float, float]:
    """The range the real figure should fall in `level` per cent of the time. It widens the
    further ahead we look (the uncertainty grows with the square root of the months ahead)."""
    half = Z[level] * sigma * math.sqrt(step)
    lower = point - half
    upper = point + half if ceiling is None else min(ceiling, point + half)
    return (max(0.0, lower) if non_negative else lower), upper
