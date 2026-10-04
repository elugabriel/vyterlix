"""The forecasting methods. Each takes a history of monthly figures (oldest first) and says what
the next `horizon` months will be.

They are deliberately simple and explainable, and they can be swapped: the service tries each
one on the business's own recent history and keeps whichever was closest (selection.py). To add a
method, write a class here, add it to METHODS and add a row to `forecast_models`.

Figures are floats: a forecast is an estimate, and is rounded to the penny only when it is saved.
"""

from abc import ABC, abstractmethod


class Method(ABC):
    code: str
    min_history: int  # months of history needed

    @abstractmethod
    def predict(self, history: list[float], horizon: int) -> list[float]:
        """The expected value for each of the next `horizon` months."""


class MovingAverage(Method):
    """Expects the future to look like the recent average. Steady and hard to fool."""

    code = "moving_average"
    min_history = 3
    window = 3

    def predict(self, history: list[float], horizon: int) -> list[float]:
        recent = history[-self.window :]
        return [sum(recent) / len(recent)] * horizon


class LinearTrend(Method):
    """Fits a straight line through the last twelve months and carries it forward, so a business
    that is growing (or shrinking) steadily is forecast to keep doing so."""

    code = "linear_trend"
    min_history = 4
    window = 12

    def predict(self, history: list[float], horizon: int) -> list[float]:
        recent = history[-self.window :]
        n = len(recent)
        mean_x = (n - 1) / 2
        mean_y = sum(recent) / n
        spread = sum((x - mean_x) ** 2 for x in range(n))
        slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(recent)) / spread
        intercept = mean_y - slope * mean_x
        return [intercept + slope * (n - 1 + step) for step in range(1, horizon + 1)]


class SeasonalNaive(Method):
    """Expects each month to repeat the same month a year earlier. Needs a full year of history,
    and wins for businesses whose year has a strong shape."""

    code = "seasonal_naive"
    min_history = 12

    def predict(self, history: list[float], horizon: int) -> list[float]:
        last_year = history[-12:]
        return [last_year[(step - 1) % 12] for step in range(1, horizon + 1)]


# In the order they are preferred when two are equally good (simplest first).
METHODS: dict[str, Method] = {m.code: m for m in (MovingAverage(), LinearTrend(), SeasonalNaive())}
