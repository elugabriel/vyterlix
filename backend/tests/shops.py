"""Small shops worked out by hand, shared by the segment and driver tests.

`bakery` has February and March 2026 (and March 2025) of sales, `costs` has the same two months of
running costs. The numbers are written beside each fixture so a test can be checked by eye.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, Supplier
from tests.test_health import scoped

D = Decimal


def _list_item(db, business, kind, name, org=0, **extra):
    with scoped(db, business, org):
        item = BusinessListItem(kind=kind, name=name, **extra)
        db.add(item)
        db.flush()
        return item.id


def _make(db, business, model, org=0, **fields):
    with scoped(db, business, org):
        record = model(**fields)
        db.add(record)
        db.flush()
        return record.id


def _sale(db, business, day, net, *, channel=None, customer=None, line=None, kind="sale", org=0):
    """`line` = (product id, quantity, cost); refunds are given as positive numbers."""
    sign = -1 if kind == "refund" else 1
    net = D(net) * sign
    with scoped(db, business, org):
        record = Sale(
            sold_on=day, kind=kind, net_amount=net, vat_amount=net / 5, gross_amount=net * D("1.2"),
            sales_channel_id=channel, customer_id=customer,
        )  # fmt: skip
        db.add(record)
        db.flush()
        if line is not None:
            product, quantity, cost = line
            db.add(
                SaleLine(
                    sale_id=record.id, product_id=product, quantity=D(quantity) * sign,
                    net_amount=net, vat_amount=net / 5, cost_amount=D(cost),
                )
            )  # fmt: skip
        db.flush()


@pytest.fixture
def bakery(db, business):
    """February and March 2026 (and March 2025), every figure below worked out by hand.

    Feb: Tue 3rd  Shop    Jo   Sourdough 300 (100 loaves, cost 150)
         Tue 10th Shop    Sam  Cake      200 (20, cost 100)
         Thu 12th Website Jo   Sourdough 100 (30, cost 50)                          = 600
    Mar: Tue 3rd  Shop    Jo   Sourdough 100 (30, cost 50)
         Tue 10th Shop    Sam  Cake      200 (20, cost 100)
         Thu 12th Website Jo   Sourdough  50 (15, cost 25)
         Sat 14th Website -    Brownie    80 (10, cost 30)    (a new product, no customer)
         Fri 20th Shop    Jo   refund of Sourdough 20 (5 back, cost 10 back)       = 410
    Mar 2025: Wed 5th Shop Jo Sourdough 500 (50, cost 250)
    """
    sourdough = _make(db, business, Product, name="Sourdough")
    cake = _make(db, business, Product, name="Cake")
    brownie = _make(db, business, Product, name="Brownie")
    shop = _list_item(db, business, "sales_channel", "Shop")
    web = _list_item(db, business, "sales_channel", "Website")
    jo = _make(db, business, Customer, name="Jo")
    sam = _make(db, business, Customer, name="Sam")
    s = lambda *a, **k: _sale(db, business, *a, **k)  # noqa: E731
    s(date(2026, 2, 3), 300, channel=shop, customer=jo, line=(sourdough, 100, 150))
    s(date(2026, 2, 10), 200, channel=shop, customer=sam, line=(cake, 20, 100))
    s(date(2026, 2, 12), 100, channel=web, customer=jo, line=(sourdough, 30, 50))
    s(date(2026, 3, 3), 100, channel=shop, customer=jo, line=(sourdough, 30, 50))
    s(date(2026, 3, 10), 200, channel=shop, customer=sam, line=(cake, 20, 100))
    s(date(2026, 3, 12), 50, channel=web, customer=jo, line=(sourdough, 15, 25))
    s(date(2026, 3, 14), 80, channel=web, line=(brownie, 10, 30))
    s(date(2026, 3, 20), 20, channel=shop, customer=jo, line=(sourdough, 5, 10), kind="refund")
    s(date(2025, 3, 5), 500, channel=shop, customer=jo, line=(sourdough, 50, 250))
    return business


def _expense(db, business, day, net, *, category=None, supplier=None, org=0):
    with scoped(db, business, org):
        net = D(net)
        db.add(
            Expense(
                spent_on=day, net_amount=net, vat_amount=net / 5, gross_amount=net * D("1.2"),
                cost_category_id=category, supplier_id=supplier,
            )
        )  # fmt: skip
        db.flush()


@pytest.fixture
def costs(db, business):
    """Feb: Rent 500 (Landlord), Utilities 100, Stock 300 (bought for resale).
    Mar: Rent 500 (Landlord), Utilities 180, Stock 600, and 50 with no category or supplier."""
    rent = _list_item(db, business, "cost_category", "Rent", is_cost_of_sales=False)
    utilities = _list_item(db, business, "cost_category", "Utilities", is_cost_of_sales=False)
    stock = _list_item(db, business, "cost_category", "Stock", is_cost_of_sales=True)
    landlord = _make(db, business, Supplier, name="Landlord")
    miller = _make(db, business, Supplier, name="Miller")
    e = lambda *a, **k: _expense(db, business, *a, **k)  # noqa: E731
    e(date(2026, 2, 1), 500, category=rent, supplier=landlord)
    e(date(2026, 2, 5), 100, category=utilities)
    e(date(2026, 2, 8), 300, category=stock, supplier=miller)
    e(date(2026, 3, 1), 500, category=rent, supplier=landlord)
    e(date(2026, 3, 5), 180, category=utilities)
    e(date(2026, 3, 8), 600, category=stock, supplier=miller)
    e(date(2026, 3, 9), 50)
    return business
