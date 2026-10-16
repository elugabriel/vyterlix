"""Small facts about the product or customer behind a finding, so the right action is matched.

A fall in one product's sales calls for a different action when the shelf is full of it than when
it is running low; a fall in one customer's orders calls for a different one when they have gone
quiet than when they still buy, only less. The change engine only knows the figures that moved, so
this looks at the stock and the sales records for the parts it named.

Tags (used by the `tag` in a library action's `addresses`):
- much_stock: a product with more than MUCH_STOCK_DAYS of stock at its recent pace of sales;
- low_stock: a product with less than LOW_STOCK_DAYS of stock;
- still_buying: a customer who bought within the last STILL_BUYING_DAYS.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.uk import today_uk
from app.models.data import Customer, Product, Sale, SaleLine, StockMovement
from app.recommend.rules import Finding, part_of

MUCH_STOCK_DAYS = 90
LOW_STOCK_DAYS = 14
STILL_BUYING_DAYS = 60
RECENT_DAYS = 90  # the pace of sales is read over this long


def _product_tags(db: Session, names: set[str], today: date) -> dict[str, frozenset[str]]:
    products = {
        p.name: p.id
        for p in db.scalars(select(Product).where(Product.name.in_(names), Product.is_active))
    }
    if not products:
        return {}
    on_hand = {
        pid: float(total or 0)
        for pid, total in db.execute(
            select(StockMovement.product_id, func.sum(StockMovement.quantity))
            .where(StockMovement.product_id.in_(products.values()), StockMovement.moved_on <= today)
            .group_by(StockMovement.product_id)
        )
    }
    since = today - timedelta(days=RECENT_DAYS)
    sold = {
        pid: float(total or 0)
        for pid, total in db.execute(
            select(SaleLine.product_id, func.sum(SaleLine.quantity))
            .join(
                Sale,
                (Sale.organization_id == SaleLine.organization_id) & (Sale.id == SaleLine.sale_id),
            )
            .where(SaleLine.product_id.in_(products.values()), Sale.sold_on > since)
            .group_by(SaleLine.product_id)
        )
    }
    out: dict[str, frozenset[str]] = {}
    for name, pid in products.items():
        if pid not in on_hand:
            continue  # stock is not recorded for this product: say nothing about it
        shelf, units = on_hand[pid], max(sold.get(pid, 0.0), 0.0)
        if shelf <= 0:
            out[name] = frozenset({"low_stock"})
            continue
        cover = None if units == 0 else shelf / (units / RECENT_DAYS)
        if cover is None or cover > MUCH_STOCK_DAYS:
            out[name] = frozenset({"much_stock"})
        elif cover < LOW_STOCK_DAYS:
            out[name] = frozenset({"low_stock"})
    return out


def _customer_tags(db: Session, names: set[str]) -> dict[str, frozenset[str]]:
    latest_sale = db.scalar(select(func.max(Sale.sold_on)))
    if latest_sale is None:
        return {}
    last = dict(
        db.execute(
            select(Customer.name, func.max(Sale.sold_on))
            .join(
                Sale,
                (Sale.organization_id == Customer.organization_id)
                & (Sale.customer_id == Customer.id),
            )
            .where(Customer.name.in_(names), Sale.kind == "sale")
            .group_by(Customer.name)
        ).all()
    )
    line = latest_sale - timedelta(days=STILL_BUYING_DAYS)
    return {name: frozenset({"still_buying"}) for name, day in last.items() if day > line}


def tag(db: Session, findings: list[Finding], today: date | None = None) -> list[Finding]:
    """The same findings, with tags added to those about a product or a customer."""
    today = today or today_uk()
    products, customers = set(), set()
    for finding in findings:
        dimension, target = part_of(finding)
        if target and dimension == "Product":
            products.add(target)
        elif target and dimension == "Customer":
            customers.add(target)
    found = {
        "Product": _product_tags(db, products, today) if products else {},
        "Customer": _customer_tags(db, customers) if customers else {},
    }
    for finding in findings:
        dimension, target = part_of(finding)
        finding.tags = found.get(dimension or "", {}).get(target or "", frozenset())
    return findings
