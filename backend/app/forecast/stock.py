"""What to stock: turning an expected number of items sold into a plain instruction.

Pure arithmetic. The expected sales of each product come from the forecasting methods
(services/stock_forecast.py); this file decides what that means for the stock on the shelf.

For one product, over a month:
- we expect to sell `expected` items, and 80 times out of 100 no more than `upper`;
- we have `on_hand` items;
- "order now" means we would run out before selling what we expect;
- "watch" means we cover the expected sales but not a busy month (up to `upper`);
- "ok" means we cover even a busy month.
"""

import math

DAYS_IN_A_MONTH = 30.4  # the average, for turning a month's sales into days of stock


def status(expected: float, upper: float, on_hand: float) -> str:
    if on_hand < expected:
        return "order_now"
    return "watch" if on_hand < upper else "ok"


def order_quantity(upper: float, on_hand: float) -> int:
    """How many more to order so a busy month (the upper end of the range) is covered; whole items,
    rounded up."""
    return max(0, math.ceil(upper - on_hand))


def days_of_cover(expected: float, on_hand: float) -> float | None:
    """How many days the stock on hand lasts at the expected pace. None when nothing is expected to
    sell (the stock would last for ever)."""
    if expected <= 0:
        return None
    return max(0.0, on_hand) / (expected / DAYS_IN_A_MONTH)


URGENCY = {"order_now": 0, "watch": 1, "ok": 2}
