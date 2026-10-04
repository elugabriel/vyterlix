"""Where did a change come from? Pure comparison of two periods, split into segments.

A figure such as sales is the sum of its parts (each product, each channel, each customer...).
Given what every part came to in two periods, this works out how much each part moved and how
much of the overall change each one accounts for. Parts that moved against the tide are kept, so
"sales fell £500" can show one product down £900 and another up £400.

No database here: the numbers come from services/segments.py.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.health.scoring import format_value

ZERO = Decimal("0")
HUNDRED = Decimal("100")
OTHER_LABEL = "Everything else"


@dataclass
class SegmentChange:
    key: str | None
    label: str
    current: Decimal
    previous: Decimal
    change: Decimal  # current - previous
    share_of_change_pct: Decimal | None  # change / total change; None when nothing moved overall
    state: str  # new | gone | up | down | steady


@dataclass
class Comparison:
    total_current: Decimal
    total_previous: Decimal
    total_change: Decimal
    total_change_pct: Decimal | None  # None when the earlier total is zero
    rows: list[SegmentChange]


def state_of(current: Decimal, previous: Decimal) -> str:
    if previous == 0 and current != 0:
        return "new"
    if current == 0 and previous != 0:
        return "gone"
    if current > previous:
        return "up"
    return "down" if current < previous else "steady"


def share_of(change: Decimal, total_change: Decimal) -> Decimal | None:
    """What share of the overall change this part is; can exceed 100% or be negative when other
    parts moved the opposite way."""
    return None if total_change == 0 else change / total_change * HUNDRED


def _row(key, label, current: Decimal, previous: Decimal, total_change: Decimal) -> SegmentChange:
    change = current - previous
    return SegmentChange(
        key, label, current, previous, change, share_of(change, total_change),
        state_of(current, previous),
    )  # fmt: skip


def compare(
    current: dict[str, tuple[str, Decimal]],
    previous: dict[str, tuple[str, Decimal]],
    limit: int = 8,
) -> Comparison:
    """`current` and `previous` map a segment key to (label, amount).

    Rows are ordered by how far they moved (biggest first, whichever way); a part that did not
    move at all is left out. Anything beyond `limit` is rolled into "Everything else".
    """
    total_current = sum((v for _, v in current.values()), ZERO)
    total_previous = sum((v for _, v in previous.values()), ZERO)
    total_change = total_current - total_previous

    rows = []
    for key in current.keys() | previous.keys():
        label = (current.get(key) or previous[key])[0]
        now = current.get(key, (label, ZERO))[1]
        before = previous.get(key, (label, ZERO))[1]
        if now == before:
            continue
        rows.append(_row(key, label, now, before, total_change))
    rows.sort(key=lambda r: (-abs(r.change), r.label))
    if len(rows) > limit:
        rest = rows[limit:]
        now = sum((r.current for r in rest), ZERO)
        before = sum((r.previous for r in rest), ZERO)
        rows = [*rows[:limit], _row(None, OTHER_LABEL, now, before, total_change)]
    pct = None if total_previous == 0 else total_change / abs(total_previous) * HUNDRED
    return Comparison(total_current, total_previous, total_change, pct, rows)


def describe_segment(
    metric_label: str, unit: str, row: SegmentChange, month: str, against: str
) -> str:
    """One plain sentence on one part: what it did, and how it fits the overall change."""
    now, before = format_value(row.current, unit), format_value(row.previous, unit)
    if row.state == "new":
        text = f"{row.label} had no {metric_label.lower()} in {against} and {now} in {month}."
    elif row.state == "gone":
        text = f"{row.label} had {before} in {against} and none in {month}."
    else:
        verb = "rose" if row.change > 0 else "fell"
        text = f"{row.label} {verb} from {before} in {against} to {now} in {month}."
    if row.share_of_change_pct is None:
        return text
    if row.share_of_change_pct < 0:
        return text + " That went against the overall change."
    return text + f" That is {row.share_of_change_pct:.0f}% of the overall change."
