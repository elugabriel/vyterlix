# ruff: noqa: E501
"""Breaking the numbers down: where sales come from (by channel, by product) and which products
move fast, slowly, or not at all.

These are read straight from the records when asked, not stored, because the answer depends on
the dates chosen. Same conventions as the KPI measures (app/kpi/measures.py): money is net of
VAT, refunds are negative and give their cost back.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.uk import today_uk
from app.kpi import periods
from app.kpi.measures import _hash_joins
from app.models.business import BusinessListItem
from app.models.data import Booking, Customer, Product, Sale, SaleLine, StockMovement
from app.schemas.kpi import (
    BookingChoicesOut,
    BreakdownOut,
    BreakdownRowOut,
    CustomerToFollowUpOut,
    FollowUpOut,
    MoverOut,
    MoversOut,
    ServiceRowOut,
    ServicesOut,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
PENNY = Decimal("0.01")
OTHER_LABEL = "Everything else"


def _pounds(value: Decimal) -> str:
    return str(Decimal(value).quantize(PENNY))


def _units(value: Decimal) -> str:
    value = Decimal(value)
    return str(value.quantize(Decimal("1"))) if value == value.to_integral_value() else str(value)


def default_range(today: date | None = None) -> tuple[date, date]:
    """The last 12 finished months."""
    this_month = periods.start_of(today or today_uk(), "month")
    return periods.shift(this_month, "month", -12), this_month - timedelta(days=1)


def _share(part: Decimal, total: Decimal) -> str | None:
    return str((part / total * HUNDRED).quantize(PENNY)) if total else None


def breakdown(
    db: Session,
    dimension: str,
    start: date | None = None,
    end: date | None = None,
    limit: int = 10,
    today: date | None = None,
) -> BreakdownOut:
    if start is None or end is None:
        default_start, default_end = default_range(today)
        start, end = start or default_start, end or default_end
    if end < start:
        raise AppError("The end date is before the start date.", code="bad_range", status_code=422)
    if dimension == "channel":
        rows = _by_channel(db, start, end)
    elif dimension == "product":
        rows = _by_product(db, start, end)
    else:
        raise AppError("Unknown breakdown.", code="bad_dimension", status_code=422)

    total = sum((r["revenue"] for r in rows), ZERO)
    rows.sort(key=lambda r: (-r["revenue"], r["label"]))
    shown, rest = rows[:limit], rows[limit:]
    if rest:
        shown.append(
            {
                "key": None,
                "label": OTHER_LABEL,
                "revenue": sum((r["revenue"] for r in rest), ZERO),
                "count": sum(r["count"] for r in rest),
                "gross_profit": (
                    sum((r["gross_profit"] for r in rest), ZERO)
                    if rows and rows[0]["gross_profit"] is not None
                    else None
                ),
            }
        )
    return BreakdownOut(
        dimension=dimension,
        period_from=start,
        period_to=end,
        total_revenue=_pounds(total),
        rows=[
            BreakdownRowOut(
                key=r["key"],
                label=r["label"],
                revenue=_pounds(r["revenue"]),
                share_pct=_share(r["revenue"], total),
                count=int(r["count"]),
                gross_profit=_pounds(r["gross_profit"]) if r["gross_profit"] is not None else None,
            )
            for r in shown
        ],
    )


def _by_channel(db: Session, start: date, end: date) -> list[dict]:
    query = (
        select(
            Sale.sales_channel_id,
            BusinessListItem.name,
            func.sum(Sale.net_amount),
            func.count().filter(Sale.kind == "sale"),
        )
        .select_from(Sale)
        .outerjoin(
            BusinessListItem,
            (BusinessListItem.organization_id == Sale.organization_id)
            & (BusinessListItem.id == Sale.sales_channel_id),
        )
        .where(Sale.sold_on >= start, Sale.sold_on <= end)
        .group_by(Sale.sales_channel_id, BusinessListItem.name)
    )
    return [
        {
            "key": channel_id,
            "label": name or "No channel recorded",
            "revenue": Decimal(revenue or 0),
            "count": count,
            "gross_profit": None,
        }
        for channel_id, name, revenue, count in db.execute(query)
    ]


def _by_product(db: Session, start: date, end: date) -> list[dict]:
    refund_sign = case((Sale.kind == "refund", -1), else_=1)
    query = (
        select(
            SaleLine.product_id,
            Product.name,
            func.sum(SaleLine.net_amount),
            func.sum(SaleLine.quantity),
            func.sum(refund_sign * func.coalesce(SaleLine.cost_amount, 0)),
        )
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
    return [
        {
            "key": product_id,
            "label": name or "Not linked to a product",
            "revenue": Decimal(revenue or 0),
            "count": Decimal(units or 0),
            "gross_profit": Decimal(revenue or 0) - Decimal(cost or 0),
        }
        for product_id, name, revenue, units, cost in found
    ]


def movers(db: Session, as_of: date | None = None, days: int = 90, limit: int = 5) -> MoversOut:
    """Fast movers, slow movers and dead stock over the last `days` days."""
    as_of = as_of or today_uk()
    window_start = as_of - timedelta(days=days - 1)
    on_hand = {
        pid: Decimal(total or 0)
        for pid, total in db.execute(
            select(StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.moved_on <= as_of)
            .group_by(StockMovement.product_id)
        )
    }
    with _hash_joins(db):
        sold = {
            pid: Decimal(units or 0)
            for pid, units in db.execute(
                select(SaleLine.product_id, func.sum(SaleLine.quantity))
                .select_from(SaleLine)
                .join(
                    Sale,
                    (Sale.organization_id == SaleLine.organization_id)
                    & (Sale.id == SaleLine.sale_id),
                )
                .where(
                    Sale.sold_on >= window_start,
                    Sale.sold_on <= as_of,
                    SaleLine.product_id.is_not(None),
                )
                .group_by(SaleLine.product_id)
            )
        }
        last_sold = dict(
            db.execute(
                select(SaleLine.product_id, func.max(Sale.sold_on))
                .select_from(SaleLine)
                .join(
                    Sale,
                    (Sale.organization_id == SaleLine.organization_id)
                    & (Sale.id == SaleLine.sale_id),
                )
                .where(Sale.kind == "sale", Sale.sold_on <= as_of, SaleLine.product_id.is_not(None))
                .group_by(SaleLine.product_id)
            ).all()
        )
    products = db.execute(
        select(Product.id, Product.name, Product.sku, Product.unit_cost).where(
            Product.is_active.is_(True)
        )
    ).all()

    def row(pid: uuid.UUID, name: str, sku: str | None, cost: Decimal | None) -> MoverOut:
        held = on_hand.get(pid, ZERO)
        last = last_sold.get(pid)
        return MoverOut(
            product_id=pid,
            name=name,
            sku=sku,
            units_sold=_units(sold.get(pid, ZERO)),
            on_hand=_units(held),
            stock_value=_pounds(max(held, ZERO) * Decimal(cost)) if cost is not None else None,
            last_sold_on=last,
            days_since_last_sale=(as_of - last).days if last else None,
        )

    rows = [row(pid, name, sku, cost) for pid, name, sku, cost in products]
    selling = sorted(
        (r for r in rows if Decimal(r.units_sold) > 0),
        key=lambda r: (-Decimal(r.units_sold), r.name),
    )
    fast = selling[:limit]
    slow = [r for r in sorted(selling, key=lambda r: (Decimal(r.units_sold), r.name))][:limit]
    slow = [r for r in slow if r not in fast] if len(selling) > limit else slow
    dead = sorted(
        (r for r in rows if Decimal(r.on_hand) > 0 and Decimal(r.units_sold) <= 0),
        key=lambda r: (-Decimal(r.stock_value or 0), r.name),
    )[:limit]
    return MoversOut(as_of=as_of, days=days, fast=fast, slow=slow, dead=dead)


QUIET_DAYS = 60  # how long without buying before a customer is worth getting in touch with


def customers_to_follow_up(
    db: Session, *, quiet_days: int = QUIET_DAYS, limit: int = 10
) -> FollowUpOut:
    """Customers who used to buy and have gone quiet, the ones who spent most first.

    "Quiet" is counted back from the latest sale the business has recorded, not from today, so a
    business that has not brought its latest sales in is not told that everyone has gone quiet.
    Customers whose sales are not linked to a customer cannot be listed, and are not guessed at.
    """
    as_of = db.scalar(select(func.max(Sale.sold_on)))
    if as_of is None:
        return FollowUpOut(as_of=None, quiet_days=quiet_days, total=0, customers=[])
    bought = (
        select(
            Sale.customer_id.label("customer_id"),
            func.max(Sale.sold_on).label("last_bought_on"),
            func.count().filter(Sale.kind == "sale").label("orders"),
            func.sum(Sale.net_amount).label("spent"),
        )
        .where(Sale.customer_id.is_not(None))
        .group_by(Sale.customer_id)
        .subquery()
    )
    cutoff = as_of - timedelta(days=quiet_days)
    quiet = (
        select(bought, Customer.name)
        .join(Customer, Customer.id == bought.c.customer_id)
        .where(bought.c.last_bought_on <= cutoff, bought.c.orders > 0, Customer.is_active.is_(True))
    )
    total = db.scalar(select(func.count()).select_from(quiet.subquery())) or 0
    rows = db.execute(quiet.order_by(bought.c.spent.desc(), Customer.name).limit(limit)).all()
    return FollowUpOut(
        as_of=as_of,
        quiet_days=quiet_days,
        total=total,
        customers=[
            CustomerToFollowUpOut(
                customer_id=r.customer_id,
                name=r.name or "A customer with no name",
                last_bought_on=r.last_bought_on,
                days_since=(as_of - r.last_bought_on).days,
                orders=r.orders,
                total_spent=_pounds(r.spent),
            )
            for r in rows
        ],
    )


# --- services (appointments) ----------------------------------------------------------------------

SERVICE_WINDOW_DAYS = 90
WEAK_REBOOKING_MIN_CUSTOMERS = 5  # fewer than this and a low rate is just chance
WEAK_REBOOKING_BELOW = Decimal(
    "0.5"
)  # a service whose rate is under half the overall one is "weak"


def _pct(part: int, whole: int) -> str | None:
    return (
        None if whole == 0 else str((Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1")))
    )


def popular_services(db: Session, *, limit: int = 10) -> ServicesOut:
    """The services booked over the last three months of appointments, most popular first.

    The window ends at the latest appointment that has already happened (not at today), so a
    business that has not entered the latest weeks is not told its services went quiet.
    """
    last = db.scalar(select(func.max(Booking.booked_for)).where(Booking.status != "booked"))
    if last is None:
        return ServicesOut(
            period_from=None, period_to=None, kept=0, rebooked_pct=None, rows=[], notes=[]
        )
    first = last - timedelta(days=SERVICE_WINDOW_DAYS)
    rows = db.execute(
        select(Booking.service, Booking.status, Booking.customer_id, Booking.booked_for).where(
            Booking.booked_for > first, Booking.booked_for <= last, Booking.status != "booked"
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    last_kept: dict[str, dict] = {}  # service -> customer -> the day of their last one kept
    for service, status, customer, day in rows:
        c = counts.setdefault(service, {"completed": 0, "cancelled": 0, "no_show": 0})
        c[status] += 1
        if status == "completed" and customer is not None:
            kept = last_kept.setdefault(service, {})
            kept[customer] = max(day, kept.get(customer, day))
    customers = {c for kept in last_kept.values() for c in kept}
    later: dict = {}  # customer -> days of every appointment they kept or have booked
    if customers:
        for customer, day in db.execute(
            select(Booking.customer_id, Booking.booked_for).where(
                Booking.customer_id.in_(customers), Booking.status.in_(("completed", "booked"))
            )
        ):
            later.setdefault(customer, []).append(day)

    def booked_again(customer, since) -> bool:
        return any(day > since for day in later.get(customer, []))

    out, kept_total, rebooked_total, people_total = (
        [],
        sum(c["completed"] for c in counts.values()),
        0,
        0,
    )
    for service, c in counts.items():
        people = last_kept.get(service, {})
        again = sum(1 for cust, day in people.items() if booked_again(cust, day))
        rebooked_total += again
        people_total += len(people)
        out.append(
            ServiceRowOut(
                service=service, kept=c["completed"], cancelled=c["cancelled"], missed=c["no_show"],
                share_pct=_pct(c["completed"], kept_total), customers=len(people),
                rebooked_pct=_pct(again, len(people)),
            )
        )  # fmt: skip
    out.sort(key=lambda r: (-r.kept, r.service.lower()))
    overall = _pct(rebooked_total, people_total)
    notes: list[str] = []
    if out and out[0].kept:
        notes.append(
            f"{out[0].service} is your most booked service: {out[0].kept} kept in the last three months."
        )
    if overall is not None:
        for row in out:
            if (
                row.customers >= WEAK_REBOOKING_MIN_CUSTOMERS
                and row.rebooked_pct is not None
                and Decimal(row.rebooked_pct) < Decimal(overall) * WEAK_REBOOKING_BELOW
            ):
                notes.append(
                    f"Few people who have {row.service} book again ({row.rebooked_pct}%, against {overall}% across all your services). Offering the next appointment before they leave can help."
                )
    return ServicesOut(
        period_from=first + timedelta(days=1), period_to=last, kept=kept_total,
        rebooked_pct=overall, rows=out[:limit], notes=notes,
    )  # fmt: skip


def booking_choices(db: Session) -> BookingChoicesOut:
    """Services and staff already used (most used first), to pick from when typing in an appointment."""

    def used(column) -> list[str]:
        rows = db.execute(
            select(column)
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column)
        )
        return [value for (value,) in rows]

    return BookingChoicesOut(services=used(Booking.service), staff=used(Booking.staff))
