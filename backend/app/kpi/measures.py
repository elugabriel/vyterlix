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
from app.models.data import (
    Booking,
    Expense,
    Opportunity,
    Product,
    Sale,
    SaleLine,
    StockMovement,
)

ZERO = Decimal("0")
# The kinds of record a measure reads, and how to tell whether the business has any at all (so a
# KPI can say "needs records you haven't added" instead of showing a misleading zero).
PRESENCE = {
    "sales": select(Sale.id),
    "expenses": select(Expense.id),
    "customer_sales": select(Sale.id).where(Sale.customer_id.is_not(None)),
    "stock": select(StockMovement.id),
    "bookings": select(Booking.id),
    "invoices": select(Sale.id).where(Sale.due_on.is_not(None)),
    "pipeline": select(Opportunity.id),
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


# --- bookings -----------------------------------------------------------------------------------
# An appointment is "finished" once it has been kept, cancelled or missed. One still to come
# (status "booked") is in none of the rates. A late cancellation is one made the day before or on
# the day. Rebooking: of the customers who kept an appointment in the period, those who have another
# appointment (kept, or still to come) on a later day than their last one in the period.

LATE_CANCEL_DAYS = 1


def _bookings(*conditions, distinct=None) -> Compute:
    def compute(db: Session, granularity: str, start: date, end: date) -> Series:
        bucket = _bucket(Booking.booked_for, granularity)
        count = func.count() if distinct is None else func.count(func.distinct(distinct))
        query = (
            select(bucket, count)
            .where(Booking.booked_for >= start, Booking.booked_for <= end, *conditions)
            .group_by(bucket)
        )
        return _decimals(db.execute(query))

    return compute


def _kept_customers(db: Session, granularity: str, start: date, end: date) -> Series:
    return _bookings(Booking.status == "completed", Booking.customer_id.is_not(None),
                     distinct=Booking.customer_id)(db, granularity, start, end)  # fmt: skip


def _rebooked_customers(db: Session, granularity: str, start: date, end: date) -> Series:
    """Customers who kept an appointment in the period and have another one after their last."""
    bucket = _bucket(Booking.booked_for, granularity)
    last = (
        select(
            Booking.customer_id.label("customer_id"),
            bucket.label("period"),
            func.max(Booking.booked_for).label("last_day"),
        )
        .where(
            Booking.booked_for >= start,
            Booking.booked_for <= end,
            Booking.status == "completed",
            Booking.customer_id.is_not(None),
        )
        .group_by(Booking.customer_id, bucket)
        .subquery()
    )
    later = (
        select(Booking.id)
        .where(
            Booking.customer_id == last.c.customer_id,
            Booking.booked_for > last.c.last_day,
            Booking.status.in_(("completed", "booked")),
        )
        .exists()
    )
    query = select(last.c.period, func.count()).where(later).group_by(last.c.period)
    return _decimals(db.execute(query))


def _late_cancellations(db: Session, granularity: str, start: date, end: date) -> Series:
    return _bookings(
        Booking.status == "cancelled",
        Booking.cancelled_on.is_not(None),
        Booking.cancelled_on >= Booking.booked_for - LATE_CANCEL_DAYS,
    )(db, granularity, start, end)


# --- leads, proposals and renewals --------------------------------------------------------------
# A lead counts in the month it came in, a proposal in the month it was sent, and a win or a loss in
# the month it was decided. The win rate is of the ones decided, so work still being chased does not
# count against it.


def _opportunities(day_column, *conditions, total=None) -> Compute:
    def compute(db: Session, granularity: str, start: date, end: date) -> Series:
        bucket = _bucket(day_column, granularity)
        query = (
            select(bucket, func.count() if total is None else func.sum(total))
            .where(day_column >= start, day_column <= end, *conditions)
            .group_by(bucket)
        )
        return _decimals(db.execute(query))

    return compute


# --- invoices -----------------------------------------------------------------------------------
# An invoice is a sale with a due day. It is owed from the day of the sale until the day it is paid
# (a sale with no paid day is still owed). Amounts are what the customer owes, including VAT.


def _owed_at(db: Session, day: date, *, overdue: bool) -> Decimal:
    query = select(func.sum(Sale.gross_amount)).where(
        Sale.kind == "sale",
        Sale.due_on.is_not(None),
        Sale.sold_on <= day,
        (Sale.paid_on.is_(None)) | (Sale.paid_on > day),
    )
    if overdue:
        query = query.where(Sale.due_on < day)
    return Decimal(db.scalar(query) or 0)


def _owed(*, overdue: bool) -> Compute:
    def compute(db: Session, granularity: str, start: date, end: date) -> Series:
        first = periods.start_of(start, granularity)
        return {
            p: _owed_at(db, periods.end_of(p, granularity), overdue=overdue)
            for p in periods.series(first, periods.start_of(end, granularity), granularity)
        }

    return compute


def _days_to_pay(db: Session, granularity: str, start: date, end: date) -> Series:
    """The average number of days invoices paid in the period took to be paid."""
    bucket = _bucket(Sale.paid_on, granularity)
    query = (
        select(bucket, func.avg(Sale.paid_on - Sale.sold_on))
        .where(
            Sale.kind == "sale",
            Sale.due_on.is_not(None),
            Sale.paid_on >= start,
            Sale.paid_on <= end,
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
        Measure("leads_in", "New leads", "pipeline", _opportunities(Opportunity.created_on)),
        Measure(
            "proposals_sent",
            "Proposals sent",
            "pipeline",
            _opportunities(Opportunity.proposal_on, Opportunity.proposal_on.is_not(None)),
        ),
        Measure(
            "deals_won",
            "Won",
            "pipeline",
            _opportunities(Opportunity.closed_on, Opportunity.stage == "won"),
        ),
        Measure(
            "deals_decided",
            "Won or lost",
            "pipeline",
            _opportunities(Opportunity.closed_on, Opportunity.stage.in_(("won", "lost"))),
        ),
        Measure(
            "value_won",
            "Value won",
            "pipeline",
            _opportunities(
                Opportunity.closed_on,
                Opportunity.stage == "won",
                total=func.coalesce(Opportunity.value, 0),
            ),
        ),
        Measure(
            "renewals_won",
            "Renewals won",
            "pipeline",
            _opportunities(
                Opportunity.closed_on, Opportunity.stage == "won", Opportunity.kind == "renewal"
            ),
        ),
        Measure(
            "renewals_decided",
            "Renewals won or lost",
            "pipeline",
            _opportunities(
                Opportunity.closed_on,
                Opportunity.stage.in_(("won", "lost")),
                Opportunity.kind == "renewal",
            ),
        ),
        Measure("invoices_outstanding", "Money owed at the end", "invoices", _owed(overdue=False)),
        Measure("invoices_overdue", "Money overdue at the end", "invoices", _owed(overdue=True)),
        Measure("average_days_to_pay", "Days invoices took to be paid", "invoices", _days_to_pay),
        Measure(
            "bookings_kept",
            "Appointments kept",
            "bookings",
            _bookings(Booking.status == "completed"),
        ),
        Measure(
            "bookings_cancelled",
            "Appointments cancelled",
            "bookings",
            _bookings(Booking.status == "cancelled"),
        ),
        Measure(
            "bookings_missed",
            "Appointments missed (no-shows)",
            "bookings",
            _bookings(Booking.status == "no_show"),
        ),
        Measure(
            "bookings_finished",
            "Appointments kept, cancelled or missed",
            "bookings",
            _bookings(Booking.status != "booked"),
        ),
        Measure("late_cancellations", "Late cancellations", "bookings", _late_cancellations),
        Measure("customers_kept", "Customers who kept an appointment", "bookings", _kept_customers),
        Measure(
            "customers_rebooked", "Customers who booked again", "bookings", _rebooked_customers
        ),
    )
}


def has_records(db: Session, dataset: str) -> bool:
    return db.scalar(select(PRESENCE[dataset].limit(1).exists())) is True


def compute_series(
    db: Session, names: set[str] | frozenset[str], granularity: str, start: date, end: date
) -> dict[str, Series]:
    """{measure: {period start: value}} for the measures asked for, over [start, end]."""
    return {name: MEASURES[name].compute(db, granularity, start, end) for name in sorted(names)}
