"""Customer and stock KPIs, sales by channel and product, and fast / slow / dead stock, checked
against numbers worked out by hand."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.business import BusinessListItem
from app.models.data import Customer, Product, Sale, SaleLine, StockMovement
from app.services import kpi_breakdown
from tests.test_kpi_engine import by_month, history, run, scoped

ORGS = "/api/v1/organizations"
D = Decimal


def customer(db, business, name, org=0):
    with scoped(db, business, org):
        record = Customer(name=name)
        db.add(record)
        db.flush()
        return record.id


def product(db, business, name, cost=None, org=0):
    with scoped(db, business, org):
        record = Product(name=name, unit_cost=D(cost) if cost is not None else None)
        db.add(record)
        db.flush()
        return record.id


def channel(db, business, name, org=0):
    with scoped(db, business, org):
        record = BusinessListItem(kind="sales_channel", name=name)
        db.add(record)
        db.flush()
        return record.id


def sell(
    db, business, day, net, *, who=None, via=None, item=None, qty=1, cost=None, kind="sale", org=0
):
    """One sale with one line. Refunds are negative, as the importer stores them."""
    with scoped(db, business, org):
        sign = -1 if kind == "refund" else 1
        net = D(net) * sign
        record = Sale(
            sold_on=day,
            kind=kind,
            net_amount=net,
            vat_amount=net / 5,
            gross_amount=net * D("1.2"),
            customer_id=who,
            sales_channel_id=via,
        )
        db.add(record)
        db.flush()
        db.add(
            SaleLine(
                sale_id=record.id,
                product_id=item,
                quantity=D(qty) * sign,
                net_amount=net,
                vat_amount=net / 5,
                cost_amount=D(cost) if cost is not None else None,
            )
        )
        db.flush()


def move(db, business, product_id, day, kind, quantity, org=0):
    with scoped(db, business, org):
        db.add(StockMovement(product_id=product_id, moved_on=day, kind=kind, quantity=D(quantity)))
        db.flush()


def value(api, business, code, month, key="value", **kw):
    return by_month(history(api, business, code, **kw).json())[month][key]


# --- customers ---------------------------------------------------------------------------


@pytest.fixture
def regulars(db, business):
    """Three customers over three months, plus one anonymous sale.

    Jan: A buys twice (100, 50), B once (40), and an anonymous sale (10).
    Feb: A (30) and C (60).  Mar: A (20), B (25), C (35), and A is refunded 10.
    """
    a, b, c = (customer(db, business, name) for name in ("Anna", "Ben", "Cara"))
    sell(db, business, date(2026, 1, 5), "100", who=a)
    sell(db, business, date(2026, 1, 20), "50", who=a)
    sell(db, business, date(2026, 1, 10), "40", who=b)
    sell(db, business, date(2026, 1, 12), "10")  # anonymous
    sell(db, business, date(2026, 2, 3), "30", who=a)
    sell(db, business, date(2026, 2, 9), "60", who=c)
    sell(db, business, date(2026, 3, 2), "20", who=a)
    sell(db, business, date(2026, 3, 3), "25", who=b)
    sell(db, business, date(2026, 3, 4), "35", who=c)
    sell(db, business, date(2026, 3, 5), "10", who=a, kind="refund")
    run(db, business, first=date(2026, 1, 1))
    return business


def test_active_new_and_returning_customers(api, regulars):
    assert value(api, regulars, "active_customers", "2026-01-01") == "2"  # Anna and Ben
    assert value(api, regulars, "new_customers", "2026-01-01") == "2"
    assert value(api, regulars, "active_customers", "2026-02-01") == "2"  # Anna and Cara
    assert value(api, regulars, "new_customers", "2026-02-01") == "1"  # Cara
    assert value(api, regulars, "returning_customers", "2026-02-01") == "1"  # Anna
    assert value(api, regulars, "active_customers", "2026-03-01") == "3"
    assert value(api, regulars, "new_customers", "2026-03-01") == "0"
    assert value(api, regulars, "returning_customers", "2026-03-01") == "3"


def test_a_refund_does_not_make_someone_an_active_customer(api, db, business):
    anna = customer(db, business, "Anna")
    sell(db, business, date(2026, 2, 3), "10", who=anna, kind="refund")  # only ever a refund
    sell(db, business, date(2026, 2, 4), "10")  # some other, anonymous sale
    run(db, business, first=date(2026, 2, 1))
    assert value(api, business, "active_customers", "2026-02-01") == "0"
    assert value(api, business, "new_customers", "2026-02-01") == "0"


def test_repeat_rate_retention_and_churn(api, regulars):
    assert value(api, regulars, "repeat_customer_rate_pct", "2026-02-01") == "50.00"  # 1 of 2
    assert value(api, regulars, "repeat_customer_rate_pct", "2026-03-01") == "100.00"
    # Feb: of Jan's 2 customers (Anna, Ben), only Anna bought again
    assert value(api, regulars, "customer_retention_pct", "2026-02-01") == "50.00"
    assert value(api, regulars, "customer_churn_pct", "2026-02-01") == "50.00"
    # Mar: of Feb's 2 customers (Anna, Cara), both bought again
    assert value(api, regulars, "customer_retention_pct", "2026-03-01") == "100.00"
    assert value(api, regulars, "customer_churn_pct", "2026-03-01") == "0.00"


def test_retention_cannot_be_worked_out_for_the_first_period(api, regulars):
    first = by_month(history(api, regulars, "customer_retention_pct").json())["2026-01-01"]
    assert first["status"] == "undefined" and first["value"] is None  # no month before it


def test_spend_per_customer_uses_net_sales_to_known_customers(api, regulars):
    assert value(api, regulars, "average_customer_value", "2026-01-01") == "95.00"  # 190 / 2
    assert value(api, regulars, "average_customer_value", "2026-02-01") == "45.00"  # 90 / 2
    assert value(api, regulars, "average_customer_value", "2026-03-01") == "23.33"  # (80-10) / 3


def test_how_many_sales_are_linked_to_a_customer(api, regulars):
    assert value(api, regulars, "customers_identified_pct", "2026-01-01") == "75.00"  # 3 of 4
    assert value(api, regulars, "customers_identified_pct", "2026-02-01") == "100.00"


def test_customer_kpis_say_so_when_no_sale_names_a_customer(api, db, business):
    sell(db, business, date(2026, 1, 5), "100")
    run(db, business, first=date(2026, 1, 1))
    for code in ("active_customers", "customer_retention_pct", "average_customer_value"):
        month = by_month(history(api, business, code).json())["2026-01-01"]
        assert month["status"] == "no_data" and month["value"] is None, code
    assert value(api, business, "customers_identified_pct", "2026-01-01") == "0.00"


def test_weekly_customer_figures_use_weeks_not_months(api, db, business):
    anna, ben = customer(db, business, "Anna"), customer(db, business, "Ben")
    sell(db, business, date(2026, 3, 9), "10", who=anna)  # Monday
    sell(db, business, date(2026, 3, 11), "10", who=ben)
    sell(db, business, date(2026, 3, 16), "10", who=anna)  # next week: Anna again, Ben gone
    run(db, business, granularity="week", first=date(2026, 3, 9), today=date(2026, 3, 25))
    weeks = by_month(history(api, business, "customer_retention_pct", granularity="week").json())
    assert weeks["2026-03-16"]["value"] == "50.00"  # 1 of last week's 2


# --- stock -------------------------------------------------------------------------------


@pytest.fixture
def warehouse(db, business):
    """P1 costs 2.00, P2 costs 5.00, P3 has no cost price.

    End of Jan: P1 6, P2 0, P3 3.  End of Feb: P1 12, P2 2, P3 2.  March (in progress): P1 0.
    Sales: January 50 (cost 20), February 40 (cost 17).
    """
    p1, p2, p3 = (
        product(db, business, "P1", "2.00"),
        product(db, business, "P2", "5.00"),
        product(db, business, "P3"),
    )
    move(db, business, p1, date(2026, 1, 2), "opening", 10)
    move(db, business, p2, date(2026, 1, 2), "opening", 4)
    move(db, business, p3, date(2026, 1, 2), "opening", 3)
    move(db, business, p1, date(2026, 1, 15), "sale", -4)
    move(db, business, p2, date(2026, 1, 15), "sale", -4)
    move(db, business, p1, date(2026, 2, 3), "delivery", 6)
    move(db, business, p2, date(2026, 2, 3), "delivery", 2)
    move(db, business, p3, date(2026, 2, 3), "write_off", -1)
    move(db, business, p1, date(2026, 3, 1), "sale", -12)
    sell(db, business, date(2026, 1, 10), "50", cost="20")
    sell(db, business, date(2026, 2, 10), "40", cost="17")
    run(db, business, first=date(2026, 1, 1))
    return business


def test_stock_at_the_end_of_each_month(api, warehouse):
    assert value(api, warehouse, "stock_units", "2026-01-01") == "9"  # 6 + 0 + 3
    assert value(api, warehouse, "stock_units", "2026-02-01") == "16"
    assert value(api, warehouse, "stock_units", "2026-03-01") == "4"  # P1 sold out


def test_stock_value_is_units_times_cost_and_ignores_products_with_no_cost(api, warehouse):
    assert value(api, warehouse, "stock_value", "2026-01-01") == "12.00"  # 6 x 2.00
    assert value(api, warehouse, "stock_value", "2026-02-01") == "34.00"  # 12 x 2 + 2 x 5
    assert value(api, warehouse, "stock_value", "2026-03-01") == "10.00"  # P3 counts for nothing


def test_the_share_of_products_that_ran_out(api, warehouse):
    assert value(api, warehouse, "out_of_stock_pct", "2026-01-01") == "33.33"  # P2
    assert value(api, warehouse, "out_of_stock_pct", "2026-02-01") == "0.00"
    assert value(api, warehouse, "out_of_stock_pct", "2026-03-01") == "33.33"  # P1


def test_stock_turnover_and_days_of_stock(api, warehouse):
    assert value(api, warehouse, "stock_turnover", "2026-01-01") == "1.67"  # 20 / 12
    assert value(api, warehouse, "stock_turnover", "2026-02-01") == "0.50"  # 17 / 34
    assert value(api, warehouse, "days_of_stock", "2026-01-01") == "18.60"  # 12 / 20 x 31 days
    assert (
        value(api, warehouse, "days_of_stock", "2026-02-01") == "56"
    )  # 34 / 17 x 28 days: whole days show as whole


def test_stock_kpis_say_so_when_there_are_no_stock_records(api, db, business):
    sell(db, business, date(2026, 1, 10), "50", cost="20")
    run(db, business, first=date(2026, 1, 1))
    for code in ("stock_units", "stock_value", "stock_turnover", "out_of_stock_pct"):
        month = by_month(history(api, business, code).json())["2026-01-01"]
        assert month["status"] == "no_data", code


def test_stock_carried_over_from_before_the_first_period_is_counted(api, db, business):
    item = product(db, business, "P1", "1.00")
    move(db, business, item, date(2025, 11, 1), "opening", 50)  # long before the window
    move(db, business, item, date(2026, 2, 5), "sale", -20)
    run(db, business, first=date(2026, 2, 1), last=date(2026, 2, 1))
    assert value(api, business, "stock_units", "2026-02-01") == "30"


def test_the_listing_says_which_records_each_kpi_needs(api, business):
    body = api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["owner"]).json()
    needs = {k["code"]: k["requires"] for k in body["kpis"]}
    assert needs["active_customers"] == ["customer_sales"]
    assert needs["stock_value"] == ["stock"]
    assert needs["net_profit"] == ["sales", "expenses"]
    categories = {k["category"] for k in body["kpis"]}
    assert {"financial", "sales", "customer", "inventory"} <= categories


# --- sales by channel and by product -----------------------------------------------------

RANGE = {"from": "2026-01-01", "to": "2026-03-31"}


def breakdown(api, business, dimension, who="owner", org=0, **params):
    url = f"{ORGS}/{business[org]}/kpis/breakdown/{dimension}"
    return api.get(url, params={**RANGE, **params}, headers=business[2][who])


@pytest.fixture
def trade(db, business):
    """Shop 100 + 50, Website 30 - 10 refunded, one sale with no channel 20.
    Products: P1 150 (3 items, cost 60), P2 20 net of a refund (cost 10 - 3 given back), and one
    line not linked to a product (20)."""
    shop, web = channel(db, business, "Shop"), channel(db, business, "Website")
    p1, p2 = product(db, business, "Sourdough"), product(db, business, "Croissant")
    sell(db, business, date(2026, 1, 5), "100", via=shop, item=p1, qty=2, cost="40")
    sell(db, business, date(2026, 1, 9), "50", via=shop, item=p1, qty=1, cost="20")
    sell(db, business, date(2026, 2, 2), "30", via=web, item=p2, qty=1, cost="10")
    sell(db, business, date(2026, 2, 6), "10", via=web, item=p2, qty=1, cost="3", kind="refund")
    sell(db, business, date(2026, 3, 3), "20")
    sell(db, business, date(2025, 12, 31), "999", via=shop, item=p1)  # outside the range
    return business


def test_sales_by_channel_with_shares_that_add_up(api, trade):
    body = breakdown(api, trade, "channel").json()
    assert body["total_revenue"] == "190.00"  # 150 + 20 + 20
    rows = {r["label"]: r for r in body["rows"]}
    assert rows["Shop"]["revenue"] == "150.00" and rows["Shop"]["share_pct"] == "78.95"
    assert rows["Shop"]["count"] == 2
    assert (
        rows["Website"]["revenue"] == "20.00" and rows["Website"]["count"] == 1
    )  # refund isn't a sale
    assert (
        rows["No channel recorded"]["revenue"] == "20.00"
        and rows["No channel recorded"]["key"] is None
    )
    assert [r["label"] for r in body["rows"]][0] == "Shop"  # biggest first
    assert all(r["gross_profit"] is None for r in body["rows"])
    total_share = sum(Decimal(r["share_pct"]) for r in body["rows"])
    assert abs(total_share - 100) < Decimal("0.1")


def test_the_rest_are_rolled_into_everything_else(api, trade):
    body = breakdown(api, trade, "channel", limit=1).json()
    assert [r["label"] for r in body["rows"]] == ["Shop", "Everything else"]
    assert body["rows"][1]["revenue"] == "40.00" and body["rows"][1]["key"] is None
    assert body["total_revenue"] == "190.00"  # the total never changes


def test_sales_by_product_with_gross_profit(api, trade):
    body = breakdown(api, trade, "product").json()
    rows = {r["label"]: r for r in body["rows"]}
    assert rows["Sourdough"]["revenue"] == "150.00" and rows["Sourdough"]["count"] == 3
    assert rows["Sourdough"]["gross_profit"] == "90.00"  # 150 - 60
    assert (
        rows["Croissant"]["revenue"] == "20.00" and rows["Croissant"]["count"] == 0
    )  # 1 sold, 1 back
    assert rows["Croissant"]["gross_profit"] == "13.00"  # 20 - (10 - 3 given back)
    assert rows["Not linked to a product"]["revenue"] == "20.00"
    assert body["total_revenue"] == "190.00"


def test_the_default_range_is_the_last_twelve_finished_months():
    assert kpi_breakdown.default_range(date(2026, 10, 2)) == (date(2025, 10, 1), date(2026, 9, 30))
    assert kpi_breakdown.default_range(date(2026, 1, 15)) == (date(2025, 1, 1), date(2025, 12, 31))


def test_bad_breakdown_requests_are_refused(api, trade):
    assert breakdown(api, trade, "customer").status_code == 422
    assert breakdown(api, trade, "channel", limit=0).status_code == 422
    res = breakdown(api, trade, "channel", **{"from": "2026-03-01", "to": "2026-01-01"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "bad_range"


def test_breakdowns_only_show_the_businesss_own_sales(api, db, trade):
    sell(db, trade, date(2026, 1, 8), "5000", via=channel(db, trade, "Their shop", org=1), org=1)
    mine = breakdown(api, trade, "channel").json()
    theirs = breakdown(api, trade, "channel", who="other", org=1).json()
    assert mine["total_revenue"] == "190.00" and theirs["total_revenue"] == "5000.00"
    assert "Their shop" not in [r["label"] for r in mine["rows"]]


def test_viewers_can_see_breakdowns(api, trade):
    assert breakdown(api, trade, "channel", who="viewer").status_code == 200


# --- fast, slow and dead stock -----------------------------------------------------------


@pytest.fixture
def shelves(db, business):
    """As of 31 Mar 2026, over 90 days (from 2 Jan): Fast sold 20, Mid 10, Slow 1. Dead has 5 in
    stock (cost 3) and no sales; Old has 2 (cost 10) and last sold in June 2025."""
    fast, mid, slow = (product(db, business, n, "1.00") for n in ("Fast", "Mid", "Slow"))
    dead, old = product(db, business, "Dead", "3.00"), product(db, business, "Old", "10.00")
    for item, amount in ((fast, 50), (mid, 50), (slow, 50), (dead, 5), (old, 2)):
        move(db, business, item, date(2025, 5, 1), "opening", amount)
    sell(db, business, date(2026, 2, 10), "20", item=fast, qty=20, cost="10")
    sell(db, business, date(2026, 3, 10), "10", item=mid, qty=10, cost="5")
    sell(db, business, date(2026, 3, 20), "1", item=slow, qty=1, cost="1")
    sell(db, business, date(2025, 6, 1), "10", item=old, qty=1, cost="5")  # outside the window
    return business


def test_fast_slow_and_dead_stock(db, shelves):
    with scoped(db, shelves):
        result = kpi_breakdown.movers(db, as_of=date(2026, 3, 31), days=90, limit=2)
    assert [m.name for m in result.fast] == ["Fast", "Mid"]
    assert [m.units_sold for m in result.fast] == ["20", "10"]
    assert [m.name for m in result.slow] == ["Slow"]  # Mid is already in "fast"
    assert [m.name for m in result.dead] == ["Old", "Dead"]  # biggest stock value first
    old, dead = result.dead
    assert (old.on_hand, old.stock_value, old.days_since_last_sale) == ("2", "20.00", 303)
    assert old.last_sold_on == date(2025, 6, 1)
    assert (dead.on_hand, dead.stock_value, dead.last_sold_on) == ("5", "15.00", None)


def test_the_movers_endpoint_works_and_is_for_everyone_who_can_see_insights(api, shelves):
    for who in ("owner", "viewer"):
        res = api.get(f"{ORGS}/{shelves[0]}/kpis/stock/movers", headers=shelves[2][who])
        assert res.status_code == 200 and set(res.json()) == {
            "as_of",
            "days",
            "fast",
            "slow",
            "dead",
        }
    bad = api.get(f"{ORGS}/{shelves[0]}/kpis/stock/movers?days=1", headers=shelves[2]["owner"])
    assert bad.status_code == 422


def test_a_product_that_was_never_sold_is_dead_stock_with_no_last_sale_date(db, business):
    item = product(db, business, "Untouched", "2.00")
    move(db, business, item, date(2026, 1, 1), "opening", 7)
    with scoped(db, business):
        result = kpi_breakdown.movers(db, as_of=date(2026, 3, 31))
    [only] = result.dead
    assert only.name == "Untouched" and only.last_sold_on is None
    assert only.days_since_last_sale is None and result.fast == [] and result.slow == []


def test_stock_to_watch_ignores_archived_products(db, business):
    item = product(db, business, "Gone", "2.00")
    move(db, business, item, date(2026, 1, 1), "opening", 7)
    with scoped(db, business):
        db.get(Product, item).is_active = False
        db.flush()
        assert kpi_breakdown.movers(db, as_of=date(2026, 3, 31)).dead == []


def test_every_new_seeded_kpi_exists(db):
    from sqlalchemy import select

    from app.models.kpi import KpiDefinition

    codes = set(db.scalars(select(KpiDefinition.code)))
    assert {
        "active_customers", "new_customers", "returning_customers", "repeat_customer_rate_pct",
        "customer_retention_pct", "customer_churn_pct", "average_customer_value",
        "customers_identified_pct", "stock_units", "stock_value", "stock_turnover",
        "days_of_stock", "out_of_stock_pct",
    } <= codes  # fmt: skip
    assert uuid.UUID(int=0)
