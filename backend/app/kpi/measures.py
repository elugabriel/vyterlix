"""Measures: the plain sums and counts every KPI is built from.

Each measure is one small SQL aggregate over a business's trading data, grouped by period
(week, month...). KPI definitions combine them with a formula, e.g. gross profit is
"revenue - cogs", so a new KPI that only recombines these needs a database row, not new code.

Conventions (they matter for the numbers, so they are written down here):
- Money is excluding VAT ("net"), the way the data is stored. Refunds are negative sales, so
  `revenue` is net sales after refunds.
- `cogs` (cost of goods sold) is the cost recorded on sale lines. A refunded line gives the
  cost back (negative), because the goods come back into stock. A line with no recorded cost
  counts as nothing here; the data-quality score is what tells the person costs are missing.
- `operating_expenses` are expenses NOT in a category marked "cost of sales" (those are
  buying the stock that `cogs` already counts, so adding both would count the goods twice).
  Credits (negative expenses) are included; uncategorised expenses count as operating.
- Dates are the business's own UK trading dates, as stored.
- A "customer" is a record sales are linked to (sales with no customer are anonymous and are not
  counted as customers). Active = bought (a sale, not a refund) in the period. New = their first
  ever sale is in the period. Retained = active this period and also the period before.
- Stock on hand at the end of a period is the sum of every stock movement up to that day. Stock
  value is units on hand (never below zero) times the product's cost price; a product with no cost
  price counts as nothing, and the data-quality score says so.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, literal_column, select, text
from sqlalchemy.orm import Session

from app.kpi import periods
from app.models.business import BusinessListItem
from app.models.data import Expense, Product, Sale, SaleLine, StockMovement

ZERO = Decimal("0")
# The kinds of record a measure reads, and how to tell whether the business has any at all (so a
# KPI can say "needs records you haven't added" instead of showing a misleading zero).
PRESENCE = {
    "sales": select(Sale.id),
    "expenses": select(Expense.id),
    "customer_sales": select(Sale.id).where(Sale.customer_id.is_not(None)),
    "stock": select(StockMovement.id),
}

Series = dict[date, Decimal]
Compute = Callable[[Session, str, date, date], Series]


@dataclass(frozen=True)
class Measure:
    code: str
    label: str
    dataset: str  # "sales" or "expenses": what must exist for it to mean anything
    compute: Compute


def _bucket(column, granularity: str):
    """The first day of the week/month/quarter/year a date falls in (weeks start on Monday)."""
    return cast(func.date_trunc(granularity, column), Date)


@contextmanager
def _hash_joins(db: Session) -> Iterator[None]:
    """Right after a bulk load Postgres can plan a sales-to-lines join as a nested loop over the
    two big tables (minutes). For these aggregates a hash join is always the right plan."""
    db.execute(text("SET LOCAL enable_nestloop = off"))
    try:
        yield
    finally:
        db.execute(text("RESET enable_nestloop"))


def _decimals(rows) -> Series:
    return {period: Decimal(value or 0) for period, value in rows}


def _sales(expression, *, kind: str | None = None) -> Compute:
    def compute(db: Session, granularity: str, start: date, end: date) -> Series:
        bucket = _bucket(Sale.sold_on, granularity)
        query = (
            select(bucket, expression)
            .where(Sale.sold_on >= start, Sale.sold_on <= end)
            .group_by(bucket)
        )
        if kind is not None:
            query = query.where(Sale.kind == kind)
        return _decimals(db.execute(query))

    return compute


def _lines(expression) -> Compute:
    def compute(db: Session, granularity: str, start: date, end: date) -> Series:
        bucket = _bucket(Sale.sold_on, granularity)
        query = (
            select(bucket, expression)
            .select_from(SaleLine)
            .join(
                Sale,
                (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
            )
            .where(Sale.sold_on >= start, Sale.sold_on <= end)
            .group_by(bucket)
        )
        with _hash_joins(db):
            return _decimals(db.execute(query))

    return compute


def _operating_expenses(db: Session, granularity: str, start: date, end: date) -> Series:
    bucket = _bucket(Expense.spent_on, granularity)
    query = (
        select(bucket, func.sum(Expense.net_amount))
        .select_from(Expense)
        .outerjoin(
            BusinessListItem,
            (BusinessListItem.organization_id == Expense.organization_id)
            & (BusinessListItem.id == Expense.cost_category_id),
        )
        .where(
            Expense.spent_on >= start,
            Expense.spent_on <= end,
            func.coalesce(BusinessListItem.is_cost_of_sales, False).is_(False),
        )
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


def _stock_purchases(db: Session, granularity: str, start: date, end: date) -> Series:
    bucket = _bucket(Expense.spent_on, granularity)
    query = (
        select(bucket, func.sum(Expense.net_amount))
        .select_from(Expense)
        .join(
            BusinessListItem,
            (BusinessListItem.organization_id == Expense.organization_id)
            & (BusinessListItem.id == Expense.cost_category_id),
        )
        .where(
            Expense.spent_on >= start,
            Expense.spent_on <= end,
            BusinessListItem.is_cost_of_sales.is_(True),
        )
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


# --- customers ----------------------------------------------------------------------------------

_INTERVAL = {"week": "1 week", "month": "1 month", "quarter": "3 months", "year": "1 year"}


def _active_customers(db: Session, granularity: str, start: date, end: date) -> Series:
    bucket = _bucket(Sale.sold_on, granularity)
    query = (
        select(bucket, func.count(func.distinct(Sale.customer_id)))
        .where(
            Sale.sold_on >= start,
            Sale.sold_on <= end,
            Sale.kind == "sale",
            Sale.customer_id.is_not(None),
        )
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


def _new_customers(db: Session, granularity: str, start: date, end: date) -> Series:
    """Customers whose very first sale (ever, not just in the window) is in the period."""
    first = (
        select(Sale.customer_id, func.min(Sale.sold_on).label("first_sale"))
        .where(Sale.kind == "sale", Sale.customer_id.is_not(None))
        .group_by(Sale.customer_id)
        .subquery()
    )
    bucket = _bucket(first.c.first_sale, granularity)
    query = (
        select(bucket, func.count())
        .where(first.c.first_sale >= start, first.c.first_sale <= end)
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


def _retained_customers(db: Session, granularity: str, start: date, end: date) -> Series:
    """Customers active in a period who were also active in the period before it."""
    earliest = periods.shift(periods.start_of(start, granularity), granularity, -1)
    bucket = _bucket(Sale.sold_on, granularity)
    pairs = (
        select(Sale.customer_id.label("customer_id"), bucket.label("period"))
        .where(
            Sale.sold_on >= earliest,
            Sale.sold_on <= end,
            Sale.kind == "sale",
            Sale.customer_id.is_not(None),
        )
        .distinct()
        .cte("customer_periods")
    )
    now, before = pairs.alias("now"), pairs.alias("before")
    interval = literal_column(f"interval '{_INTERVAL[granularity]}'")
    query = (
        select(now.c.period, func.count())
        .select_from(now)
        .join(
            before,
            (before.c.customer_id == now.c.customer_id)
            & (before.c.period == cast(now.c.period - interval, Date)),
        )
        .where(now.c.period >= start)
        .group_by(now.c.period)
    )
    return _decimals(db.execute(query))


def _identified_revenue(db: Session, granularity: str, start: date, end: date) -> Series:
    bucket = _bucket(Sale.sold_on, granularity)
    query = (
        select(bucket, func.sum(Sale.net_amount))
        .where(Sale.sold_on >= start, Sale.sold_on <= end, Sale.customer_id.is_not(None))
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


def _identified_sales(db: Session, granularity: str, start: date, end: date) -> Series:
    bucket = _bucket(Sale.sold_on, granularity)
    query = (
        select(bucket, func.count())
        .where(
            Sale.sold_on >= start,
            Sale.sold_on <= end,
            Sale.kind == "sale",
            Sale.customer_id.is_not(None),
        )
        .group_by(bucket)
    )
    return _decimals(db.execute(query))


# --- stock --------------------------------------------------------------------------------------


def _stock_positions(
    db: Session, granularity: str, start: date, end: date
) -> tuple[dict[date, dict], dict]:
    """Units on hand per product at the end of each period, and each product's cost price."""
    first = periods.start_of(start, granularity)
    base = {
        product_id: Decimal(total or 0)
        for product_id, total in db.execute(
            select(StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.moved_on < first)
            .group_by(StockMovement.product_id)
        )
    }
    bucket = _bucket(StockMovement.moved_on, granularity)
    changes: dict[date, dict] = {}
    for product_id, period, total in db.execute(
        select(StockMovement.product_id, bucket, func.sum(StockMovement.quantity))
        .where(StockMovement.moved_on >= first, StockMovement.moved_on <= end)
        .group_by(StockMovement.product_id, bucket)
    ):
        changes.setdefault(period, {})[product_id] = Decimal(total or 0)
    on_hand, positions = dict(base), {}
    for period in periods.series(first, periods.start_of(end, granularity), granularity):
        for product_id, change in changes.get(period, {}).items():
            on_hand[product_id] = on_hand.get(product_id, ZERO) + change
        positions[period] = dict(on_hand)
    costs = {
        product_id: Decimal(cost or 0)
        for product_id, cost in db.execute(select(Product.id, Product.unit_cost))
    }
    return positions, costs


def _stock_units(db: Session, granularity: str, start: date, end: date) -> Series:
    positions, _ = _stock_positions(db, granularity, start, end)
    return {p: sum(units.values(), ZERO) for p, units in positions.items()}


def _stock_value(db: Session, granularity: str, start: date, end: date) -> Series:
    positions, costs = _stock_positions(db, granularity, start, end)
    return {
        p: sum((max(u, ZERO) * costs.get(pid, ZERO) for pid, u in units.items()), ZERO)
        for p, units in positions.items()
    }


def _products_tracked(db: Session, granularity: str, start: date, end: date) -> Series:
    positions, _ = _stock_positions(db, granularity, start, end)
    return {p: Decimal(len(units)) for p, units in positions.items()}


def _products_out_of_stock(db: Session, granularity: str, start: date, end: date) -> Series:
    positions, _ = _stock_positions(db, granularity, start, end)
    return {p: Decimal(sum(1 for u in units.values() if u <= 0)) for p, units in positions.items()}


def _period_days(db: Session, granularity: str, start: date, end: date) -> Series:
    first = periods.start_of(start, granularity)
    return {
        p: Decimal((periods.end_of(p, granularity) - p).days + 1)
        for p in periods.series(first, periods.start_of(end, granularity), granularity)
    }


_refund_sign = case((Sale.kind == "refund", -1), else_=1)

MEASURES: dict[str, Measure] = {
    m.code: m
    for m in (
        Measure("revenue", "Net sales after refunds", "sales", _sales(func.sum(Sale.net_amount))),
        Measure(
            "gross_sales",
            "Sales including VAT, after refunds",
            "sales",
            _sales(func.sum(Sale.gross_amount)),
        ),
        Measure(
            "sales_revenue",
            "Net sales before refunds",
            "sales",
            _sales(func.sum(Sale.net_amount), kind="sale"),
        ),
        Measure("sales_count", "Number of sales", "sales", _sales(func.count(), kind="sale")),
        Measure("refund_count", "Number of refunds", "sales", _sales(func.count(), kind="refund")),
        Measure(
            "units_sold",
            "Items sold, after refunds",
            "sales",
            _lines(func.sum(SaleLine.quantity)),
        ),
        Measure(
            "cogs",
            "Cost of goods sold",
            "sales",
            _lines(func.sum(_refund_sign * func.coalesce(SaleLine.cost_amount, 0))),
        ),
        Measure(
            "operating_expenses",
            "Running costs (not stock purchases)",
            "expenses",
            _operating_expenses,
        ),
        Measure(
            "stock_purchases",
            "Stock bought (costs marked as cost of sales)",
            "expenses",
            _stock_purchases,
        ),
        Measure("active_customers", "Customers who bought", "customer_sales", _active_customers),
        Measure(
            "new_customers", "Customers buying for the first time", "customer_sales", _new_customers
        ),
        Measure(
            "retained_customers",
            "Customers who bought this period and the one before",
            "customer_sales",
            _retained_customers,
        ),
        Measure(
            "identified_revenue",
            "Net sales to known customers",
            "customer_sales",
            _identified_revenue,
        ),
        Measure("identified_sales", "Sales linked to a customer", "sales", _identified_sales),
        Measure("stock_units", "Items in stock at the end", "stock", _stock_units),
        Measure("stock_value", "Value of stock at cost, at the end", "stock", _stock_value),
        Measure("products_tracked", "Products with stock records", "stock", _products_tracked),
        Measure("products_out_of_stock", "Products out of stock", "stock", _products_out_of_stock),
        Measure("period_days", "Days in the period", "sales", _period_days),
    )
}


def has_records(db: Session, dataset: str) -> bool:
    return db.scalar(select(PRESENCE[dataset].limit(1).exists())) is True


def compute_series(
    db: Session, names: set[str] | frozenset[str], granularity: str, start: date, end: date
) -> dict[str, Series]:
    """{measure: {period start: value}} for the measures asked for, over [start, end]."""
    return {name: MEASURES[name].compute(db, granularity, start, end) for name in sorted(names)}
