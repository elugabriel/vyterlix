"""What drove a change? Pure arithmetic that splits a change into causes, no database.

A change in sales can be looked at through different lenses, and each lens splits the SAME total
change into two pieces that add up to it exactly:

- days: more (or fewer) days in the month, against busier (or slower) days;
- orders: more (or fewer) sales, against a bigger (or smaller) average sale;
- price and volume: selling more (or fewer) items, against the prices actually charged.

The lenses are different ways of reading one change, so they are not added together.

The rule for each lens is the classic one: the first factor's change is valued at the OLD level
of the second, and the second factor's change gets whatever is left. That way nothing is lost
and nothing is counted twice: the two effects always add up to the total, to the penny.
"""

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
# A single part of a figure (one product, one channel...) is only called a driver when it
# accounts for at least this share of the change.
MIN_CONTRIBUTOR_SHARE = Decimal("25")


def share_of(amount: Decimal, total_change: Decimal) -> Decimal | None:
    """What share of the total change an effect is; None when nothing changed overall."""
    return None if total_change == 0 else Decimal(amount) / Decimal(total_change) * HUNDRED


def two_factors(
    count_before: Decimal, count_after: Decimal, rate_before: Decimal, rate_after: Decimal
) -> tuple[Decimal, Decimal]:
    """Split the change in (count x rate) into (the effect of the count, the effect of the rate).

    The count's change is valued at the old rate; the rate gets the rest, so the two add up to
    the total exactly. If there was no count before there is no old rate to value it at, so
    everything is the count's.
    """
    count_before, count_after = Decimal(count_before), Decimal(count_after)
    rate_before, rate_after = Decimal(rate_before), Decimal(rate_after)
    total = count_after * rate_after - count_before * rate_before
    if count_before == 0:
        return total, ZERO
    count_effect = (count_after - count_before) * rate_before
    return count_effect, total - count_effect


@dataclass
class PriceVolume:
    volume: Decimal  # the effect of selling more or fewer items, at the old prices
    price: Decimal  # the effect of the prices actually charged
    # per product: (key, volume effect, price effect)
    parts: list[tuple[str, Decimal, Decimal]]


def price_volume(rows: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]]) -> PriceVolume:
    """`rows` maps a product to (items before, sales before, items after, sales after).

    A product's price is what customers actually paid per item (sales divided by items, so
    discounts count). A product sold in only one of the two months has no price to compare, so
    all of its change counts as volume.
    """
    volume = price = ZERO
    parts = []
    for key, (q0, net0, q1, net1) in rows.items():
        total = Decimal(net1) - Decimal(net0)
        if Decimal(q0) == 0 or Decimal(q1) == 0:
            v, p = total, ZERO
        else:
            v = (Decimal(q1) - Decimal(q0)) * Decimal(net0) / Decimal(q0)  # at the old price
            p = total - v
        volume += v
        price += p
        parts.append((key, v, p))
    return PriceVolume(volume, price, parts)


def days_in(month_start, month_end) -> int:
    return (month_end - month_start).days + 1
