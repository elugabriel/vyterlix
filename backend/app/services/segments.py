"""Segment analysis: split a figure into its parts to see where a change came from.

"Sales fell £500 in March" becomes "Sourdough fell £900, the website rose £400...". The parts
are read straight from the records when asked (nothing is stored: the answer depends on the
month and what it is compared with), using the same conventions as the KPI measures
(app/kpi/measures.py): money is net of VAT, refunds are negative, a refunded line gives its cost
back. So the parts always add up to the figure on the Key figures page; where some sales have no
product detail, a row says so rather than letting the parts quietly fall short.

The comparing is pure (app/diagnostics/segments.py). This file is the questions put to the
database, one per metric and dimension.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.diagnostics import segments as compare_rules
from app.health.scoring import format_value
from app.kpi import periods
from app.kpi.measures import _hash_joins
from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, Supplier
from app.schemas.diagnostics import SegmentOut, SegmentRowOut

ZERO = Decimal("0")
PENNY = Decimal("0.01")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NO_DETAIL = "no_detail"

Totals = dict[str, tuple[str, Decimal]]  # segment key -> (label, amount)


def _key(value) -> str:
    return "none" if value is None else str(value)


def _by_label(rows, none_label: str) -> Totals:
    """Rows of (id, name, amount) into Totals, with a clear label where there is no id (or the
    record has no name)."""
    out: Totals = {}
    for ident, name, amount in rows:
        label = name or (none_label if ident is None else "Unnamed")
        out[_key(ident)] = (label, Decimal(amount or 0))
    return out


# --- sales -------------------------------------------------------------------------------------


def _sales_by_channel(db: Session, start: date, end: date, counting: bool) -> Totals:
    value = func.count().filter(Sale.kind == "sale") if counting else func.sum(Sale.net_amount)
    query = (
        select(Sale.sales_channel_id, BusinessListItem.name, value)
        .select_from(Sale)
        .outerjoin(
            BusinessListItem,
            (BusinessListItem.organization_id == Sale.organization_id)
            & (BusinessListItem.id == Sale.sales_channel_id),
        )
        .where(Sale.sold_on >= start, Sale.sold_on <= end)
        .group_by(Sale.sales_channel_id, BusinessListItem.name)
    )
    return _by_label(db.execute(query), "No channel recorded")


def _sales_by_customer(db: Session, start: date, end: date, counting: bool) -> Totals:
    value = func.count().filter(Sale.kind == "sale") if counting else func.sum(Sale.net_amount)
    query = (
        select(Sale.customer_id, Customer.name, value)
        .select_from(Sale)
        .outerjoin(
            Customer,
            (Customer.organization_id == Sale.organization_id) & (Customer.id == Sale.customer_id),
        )
        .where(Sale.sold_on >= start, Sale.sold_on <= end)
        .group_by(Sale.customer_id, Customer.name)
    )
    return _by_label(db.execute(query), "No customer recorded")


def _sales_by_weekday(db: Session, start: date, end: date, counting: bool) -> Totals:
    value = func.count().filter(Sale.kind == "sale") if counting else func.sum(Sale.net_amount)
    day = func.extract("isodow", Sale.sold_on)
    query = select(day, value).where(Sale.sold_on >= start, Sale.sold_on <= end).group_by(day)
    return {
        str(int(number)): (WEEKDAYS[int(number) - 1], Decimal(amount or 0))
        for number, amount in db.execute(query)
    }


def _sales_total(db: Session, start: date, end: date) -> Decimal:
    return Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Sale.net_amount), 0)).where(
                Sale.sold_on >= start, Sale.sold_on <= end
            )
        )
    )


def _by_product(db: Session, start: date, end: date, what: str) -> Totals:
    """`what`: "revenue", "units" or "profit", read from the sale lines."""
    refund_sign = case((Sale.kind == "refund", -1), else_=1)
    amount = {
        "revenue": func.sum(SaleLine.net_amount),
        "units": func.sum(SaleLine.quantity),
        "profit": func.sum(SaleLine.net_amount)
        - func.sum(refund_sign * func.coalesce(SaleLine.cost_amount, 0)),
    }[what]
    query = (
        select(SaleLine.product_id, Product.name, amount, func.sum(SaleLine.net_amount))
        .select_from(SaleLine)
        .join(
            Sale,
            (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
        )
        .outerjoin(
            Product,
            (Product.organization_id == SaleLine.organization_id)
            & (Product.id == SaleLine.product_id),
        )
        .where(Sale.sold_on >= start, Sale.sold_on <= end)
        .group_by(SaleLine.product_id, Product.name)
    )
    with _hash_joins(db):
        found = db.execute(query).all()
    totals = _by_label(
        [(ident, name, value) for ident, name, value, _ in found], "Not linked to a product"
    )
    if what != "units":
        # A sale recorded without lines still counts as sales; say so, so the parts add up.
        lines_net = sum((Decimal(net or 0) for *_, net in found), ZERO)
        gap = _sales_total(db, start, end) - lines_net
        if gap != 0:
            totals[NO_DETAIL] = ("Sales with no product detail", gap)
    return totals


# --- running costs -----------------------------------------------------------------------------


def _operating_expenses(db: Session, start: date, end: date, by: str) -> Totals:
    """Running costs: expenses not marked as cost of sales (those are the cost of goods sold)."""
    if by == "category":
        group, name, none_label = BusinessListItem.id, BusinessListItem.name, "No category"
    else:
        group, name, none_label = Supplier.id, Supplier.name, "No supplier"
    query = (
        select(group, name, func.sum(Expense.net_amount))
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
        .group_by(group, name)
    )
    if by == "supplier":
        query = query.outerjoin(
            Supplier,
            (Supplier.organization_id == Expense.organization_id)
            & (Supplier.id == Expense.supplier_id),
        )
    return _by_label(db.execute(query), none_label)


# --- what can be split, and how ----------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str
    # dimension -> (label for the page, how to read it)
    dimensions: dict[str, tuple[str, Callable[[Session, date, date], Totals]]]


METRICS: dict[str, Metric] = {
    "revenue": Metric(
        "Sales",
        "gbp",
        {
            "product": ("Product", lambda db, s, e: _by_product(db, s, e, "revenue")),
            "channel": ("Sales channel", lambda db, s, e: _sales_by_channel(db, s, e, False)),
            "customer": ("Customer", lambda db, s, e: _sales_by_customer(db, s, e, False)),
            "weekday": ("Day of the week", lambda db, s, e: _sales_by_weekday(db, s, e, False)),
        },
    ),
    "sales_count": Metric(
        "Number of sales",
        "count",
        {
            "channel": ("Sales channel", lambda db, s, e: _sales_by_channel(db, s, e, True)),
            "customer": ("Customer", lambda db, s, e: _sales_by_customer(db, s, e, True)),
            "weekday": ("Day of the week", lambda db, s, e: _sales_by_weekday(db, s, e, True)),
        },
    ),
    "units_sold": Metric(
        "Items sold",
        "count",
        {"product": ("Product", lambda db, s, e: _by_product(db, s, e, "units"))},
    ),
    "gross_profit": Metric(
        "Gross profit",
        "gbp",
        {"product": ("Product", lambda db, s, e: _by_product(db, s, e, "profit"))},
    ),
    "operating_expenses": Metric(
        "Running costs",
        "gbp",
        {
            "category": (
                "Cost category",
                lambda db, s, e: _operating_expenses(db, s, e, "category"),
            ),
            "supplier": ("Supplier", lambda db, s, e: _operating_expenses(db, s, e, "supplier")),
        },
    ),
}
AGAINST = {"previous_month": -1, "last_year": -12}
AGAINST_LABEL = {"previous_month": "the month before", "last_year": "the same month last year"}


def available() -> dict[str, list[str]]:
    """Which figures can be split, and by what."""
    return {code: list(metric.dimensions) for code, metric in METRICS.items()}


def _pounds_or_count(value: Decimal, unit: str) -> str:
    value = Decimal(value)
    if unit == "gbp":
        return str(value.quantize(PENNY))
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return str(value.quantize(PENNY))


def _month_name(month: date) -> str:
    return f"{month:%B %Y}"


def segment_change(
    db: Session,
    metric: str,
    dimension: str,
    month: date,
    against: str = "previous_month",
    limit: int = 8,
) -> SegmentOut:
    if metric not in METRICS:
        raise AppError("That figure can't be split up.", code="bad_metric", status_code=422)
    spec = METRICS[metric]
    if dimension not in spec.dimensions:
        raise AppError(
            f"{spec.label} can't be split by that. Choose from: {', '.join(spec.dimensions)}.",
            code="bad_dimension",
            status_code=422,
        )
    if against not in AGAINST:
        raise AppError(
            "Compare with the month before or last year.", code="bad_against", status_code=422
        )
    month = periods.start_of(month, "month")
    before = periods.shift(month, "month", AGAINST[against])
    dimension_label, read = spec.dimensions[dimension]
    now = read(db, month, periods.end_of(month, "month"))
    earlier = read(db, before, periods.end_of(before, "month"))
    result = compare_rules.compare(now, earlier, limit)

    this_name, before_name = _month_name(month), _month_name(before)
    unit = spec.unit
    total_change = result.total_change
    if total_change == 0:
        headline = f"{spec.label} in {this_name} was the same as in {before_name}."
    else:
        verb = "rose" if total_change > 0 else "fell"
        size = format_value(abs(total_change), unit)
        pct = "" if result.total_change_pct is None else f" ({abs(result.total_change_pct):.0f}%)"
        headline = f"{spec.label} {verb} by {size}{pct} in {this_name} compared with {before_name}."
    return SegmentOut(
        metric=metric,
        metric_label=spec.label,
        unit=unit,
        dimension=dimension,
        dimension_label=dimension_label,
        month=month,
        compared_with=before,
        against=against,
        total_current=_pounds_or_count(result.total_current, unit),
        total_previous=_pounds_or_count(result.total_previous, unit),
        total_change=_pounds_or_count(total_change, unit),
        total_change_pct=None
        if result.total_change_pct is None
        else str(result.total_change_pct.quantize(Decimal("0.1"))),
        headline=headline,
        rows=[
            SegmentRowOut(
                key=r.key,
                label=r.label,
                current=_pounds_or_count(r.current, unit),
                previous=_pounds_or_count(r.previous, unit),
                change=_pounds_or_count(r.change, unit),
                share_of_change_pct=None
                if r.share_of_change_pct is None
                else str(r.share_of_change_pct.quantize(Decimal("0.1"))),
                state=r.state,
                text=compare_rules.describe_segment(spec.label, unit, r, this_name, before_name),
            )
            for r in result.rows
        ],
    )
