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
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, select, text
from sqlalchemy.orm import Session

from app.models.business import BusinessListItem
from app.models.data import Expense, Sale, SaleLine

ZERO = Decimal("0")
# The kinds of record a measure reads (used to say "this business has no expenses at all").
DATASET_TABLES = {"sales": Sale, "expenses": Expense}

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
    )
}


def has_records(db: Session, dataset: str) -> bool:
    table = DATASET_TABLES[dataset]
    return db.scalar(select(select(table.id).limit(1).exists())) is True


def compute_series(
    db: Session, names: set[str] | frozenset[str], granularity: str, start: date, end: date
) -> dict[str, Series]:
    """{measure: {period start: value}} for the measures asked for, over [start, end]."""
    return {name: MEASURES[name].compute(db, granularity, start, end) for name in sorted(names)}
