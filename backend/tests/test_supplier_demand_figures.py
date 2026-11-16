# ruff: noqa: E501, F811
"""Supplier Performance and Demand Forecasting figures, checked against numbers worked out by hand."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.data import Booking, Expense, Supplier
from app.plain.figures import WORDS
from tests.shops import _expense, _sale
from tests.test_booking_figures import kpi_codes, say
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped

D = Decimal
ORGS = "/api/v1/organizations"

SUPPLIER = {
    "supplier_bought",
    "suppliers_used",
    "top_supplier_share_pct",
    "supplier_credit_rate_pct",
}
DEMAND = {
    "sales_count_growth_pct", "sales_count_vs_last_year_pct", "units_growth_pct",
    "bookings_growth_pct", "bookings_vs_last_year_pct",
}  # fmt: skip


def supplier(db, business, name):
    with scoped(db, business):
        record = Supplier(name=name)
        db.add(record)
        db.flush()
        return record.id


def credit(db, business, day, net, who):
    with scoped(db, business):
        net = D(net)
        db.add(
            Expense(
                spent_on=day, kind="credit", net_amount=-net, vat_amount=-net / 5,
                gross_amount=-net * D("1.2"), supplier_id=who,
            )
        )  # fmt: skip
        db.flush()


def book(db, business, day):
    with scoped(db, business):
        db.add(Booking(booked_for=day, service="Cut", status="completed"))
        db.flush()


@pytest.fixture
def buying(db, business):
    """September 2026 by hand: Aqua 300 and 100, Bloom 100, and Aqua gave 40 back as credit.

    Bought 500 from 2 suppliers. The biggest, Aqua, took 400 = 80%. Credit 40 of 500 = 8%.
    """
    aqua, bloom = supplier(db, business, "Aqua"), supplier(db, business, "Bloom")
    _expense(db, business, date(2026, 9, 2), 300, supplier=aqua)
    _expense(db, business, date(2026, 9, 9), 100, supplier=aqua)
    _expense(db, business, date(2026, 9, 12), 100, supplier=bloom)
    _expense(db, business, date(2026, 9, 20), 70)  # no supplier named: not counted here
    credit(db, business, date(2026, 9, 25), 40, aqua)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, business, code):
    return value(api, business, code, "2026-09-01")


def test_the_supplier_figures_match_the_hand_worked_numbers(api, buying):
    assert sept(api, buying, "supplier_bought") == "500.00"
    assert sept(api, buying, "suppliers_used") == "2"
    assert sept(api, buying, "top_supplier_share_pct") == "80.00"
    assert sept(api, buying, "supplier_credit_rate_pct") == "8.00"


def test_costs_with_no_supplier_named_do_not_count_as_supplier_buying(api, buying):
    assert sept(api, buying, "supplier_bought") == "500.00"  # not 570


def test_without_a_supplier_named_the_supplier_figures_are_not_shown(api, db, business):
    _expense(db, business, date(2026, 9, 2), 100)
    assert not SUPPLIER & kpi_codes(api, business)


def test_the_supplier_module_appears_once_its_figures_do(api, buying):
    body = api.get(f"{ORGS}/{buying[0]}/kpis", headers=buying[2]["owner"]).json()
    assert "suppliers" in {m["code"] for m in body["modules"]}


@pytest.fixture
def demand(db, business):
    """Sales: September 2025 two, August 2026 two, September 2026 three.
    Appointments: the same counts.

    This September's three against August's two is 50% more; against last September's two, also 50%.
    """
    for day in (date(2025, 9, 3), date(2025, 9, 20), date(2026, 8, 4), date(2026, 8, 19)):
        _sale(db, business, day, 100)
        book(db, business, day)
    for day in (date(2026, 9, 2), date(2026, 9, 10), date(2026, 9, 21)):
        _sale(db, business, day, 100)
        book(db, business, day)
    run(db, business, first=date(2025, 9, 1), today=date(2026, 11, 15))
    return business


def test_the_demand_figures_match_the_hand_worked_numbers(api, demand):
    assert sept(api, demand, "sales_count_growth_pct") == "50.00"
    assert sept(api, demand, "sales_count_vs_last_year_pct") == "50.00"
    assert sept(api, demand, "bookings_growth_pct") == "50.00"
    assert sept(api, demand, "bookings_vs_last_year_pct") == "50.00"


def test_the_demand_module_appears_once_its_figures_do(api, demand):
    body = api.get(f"{ORGS}/{demand[0]}/kpis", headers=demand[2]["owner"]).json()
    assert "demand" in {m["code"] for m in body["modules"]}


def test_a_shop_is_not_shown_the_appointment_demand_figures(api, db, business):
    say(api, business, "products")
    _sale(db, business, date(2026, 9, 3), 100)
    codes = kpi_codes(api, business)
    assert not {"bookings_growth_pct", "bookings_vs_last_year_pct"} & codes


def test_every_new_figure_has_plain_words_and_no_cause_is_claimed():
    for code in SUPPLIER | DEMAND:
        for say_ in (WORDS[code].up, WORDS[code].down):
            assert say_.says.strip() and say_.why.strip()
            assert "because" not in (say_.says + say_.why).lower()
