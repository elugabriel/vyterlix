"""Inventory requirements: how much of each product to have, from how fast it sells.

For every product with enough sales history, the same forecasting methods used for the other
figures (app/forecast) are tried on the product's own monthly sales, the closest one is kept, and
its expectation for the coming month is set beside what is on the shelf now. The answer is a plain
instruction per product (order now, watch, ok), how many days the stock will last, and how many to
order so a busy month is covered.

Read straight from the records when asked, with the KPI conventions (refunded items count back,
stock is the sum of every movement). Nothing is stored: stock changes daily, so a saved answer would
be stale by tomorrow. (Because it is not stored, it is not scored against what happened the way the
other forecasts are.)
"""

import math
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import Date as SqlDate
from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session

from app.core.uk import today_uk
from app.forecast import selection
from app.forecast import stock as rules
from app.forecast.models import METHODS
from app.kpi import periods
from app.kpi.measures import _hash_joins
from app.models.data import Product, Sale, SaleLine, StockMovement
from app.schemas.forecast import StockNeedOut, StockRequirementsOut
from app.services.forecast import GRANULARITY, season_factors

LEVEL = selection.INTERVAL_LEVEL
PENNY = Decimal("0.01")


def _monthly_units(db: Session, before: date) -> dict:
    """{product id: {month start: items sold}} for every finished month, refunds counted back."""
    bucket = cast(func.date_trunc("month", Sale.sold_on), SqlDate)
    query = (
        select(SaleLine.product_id, bucket, func.sum(SaleLine.quantity))
        .select_from(SaleLine)
        .join(
            Sale,
            (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
        )
        .where(Sale.sold_on < before, SaleLine.product_id.is_not(None))
        .group_by(SaleLine.product_id, bucket)
    )
    with _hash_joins(db):
        found = db.execute(query).all()
    out: dict = defaultdict(dict)
    for product_id, month, units in found:
        out[product_id][month] = float(units or 0)
    return out


def _on_hand(db: Session, today: date) -> dict:
    return {
        pid: float(total or 0)
        for pid, total in db.execute(
            select(StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.moved_on <= today)
            .group_by(StockMovement.product_id)
        )
    }


def _series(by_month: dict, last: date) -> list[tuple[date, float]]:
    """From the product's first month with sales to the last finished month; a month with no
    sales in between is a month of zero."""
    first = min(by_month)
    months = periods.series(first, last, GRANULARITY)
    return [(m, by_month.get(m, 0.0)) for m in months]


def requirements(db: Session, today: date | None = None) -> StockRequirementsOut:
    today = today or today_uk()
    this_month = periods.start_of(today, GRANULARITY)
    last = periods.shift(this_month, GRANULARITY, -1)  # the last finished month
    units = _monthly_units(db, this_month)
    on_hand = _on_hand(db, today)
    products = db.scalars(select(Product).where(Product.is_active.is_(True))).all()

    rows: list[StockNeedOut] = []
    skipped = 0
    for product in products:
        by_month = units.get(product.id)
        if not by_month:
            skipped += 1
            continue
        series = _series(by_month, last)
        months = [m for m, _ in series]
        factors, _ = season_factors(db, [*months, this_month])
        history = [v / factors[m] for m, v in series]
        scores = [selection.backtest(m, history) for m in selection.eligible(history)]
        best = selection.choose(scores)
        if best is None:
            skipped += 1
            continue
        sigma = selection.spread(best, history)
        point = max(0.0, METHODS[best.code].predict(history, 1)[0])
        lower, upper = selection.interval(point, sigma, 1, LEVEL)
        factor = factors[this_month]
        expected, high, low = point * factor, upper * factor, lower * factor
        shelf = on_hand.get(product.id, 0.0)
        # Whole items: round the expectation to the nearest, and a busy month up
        expected_items, high_items = round(expected), math.ceil(high)
        state = rules.status(expected_items, high_items, shelf)
        cover = rules.days_of_cover(expected, shelf)
        rows.append(
            StockNeedOut(
                product_id=product.id,
                name=product.name,
                sku=product.sku,
                expected_units=expected_items,
                lower_units=math.floor(low),
                upper_units=high_items,
                on_hand=round(shelf),
                days_of_cover=None if cover is None else round(cover),
                status=state,
                order_suggested=rules.order_quantity(high_items, shelf),
                method=METHODS[best.code].code,
                history_months=len(history),
                typical_miss_pct=None if best.mape is None else f"{best.mape:.0f}",
            )
        )
    rows.sort(
        key=lambda r: (
            rules.URGENCY[r.status],
            9999 if r.days_of_cover is None else r.days_of_cover,
            r.name.lower(),
        )
    )
    counts = {s: sum(1 for r in rows if r.status == s) for s in rules.URGENCY}
    return StockRequirementsOut(
        month=this_month,
        as_of=last,
        level=LEVEL,
        order_now=counts["order_now"],
        watch=counts["watch"],
        ok=counts["ok"],
        not_enough_history=skipped,
        headline=_headline(counts, skipped),
        note=(
            "Expected sales are for the whole of the current month. The range is how much we "
            f"expect to sell {LEVEL} times out of 100 at most; 'order' covers that busy-month "
            "figure. Stock on hand is every delivery, sale, write-off and adjustment so far."
        ),
        rows=rows,
    )


def _headline(counts: dict, skipped: int) -> str:
    total = sum(counts.values())
    if not total:
        return (
            "Not enough sales history yet to say how much to stock: we need at least "
            f"{selection.MIN_HISTORY} finished months of sales for a product."
        )
    parts = []
    if counts["order_now"]:
        parts.append(
            f"{counts['order_now']} product{'s' if counts['order_now'] != 1 else ''} to order now"
        )
    if counts["watch"]:
        parts.append(f"{counts['watch']} to watch")
    parts.append(f"{counts['ok']} well stocked")
    text = ", ".join(parts)
    if skipped:
        text += f". {skipped} product{'s' if skipped != 1 else ''} without enough sales history yet"
    return text[0].upper() + text[1:] + "."
