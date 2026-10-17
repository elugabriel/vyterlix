# ruff: noqa: E501, F811
"""Invoices: money owed, overdue, and how long customers take to pay (the SME and service side of
client feedback section 6)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.models.data import Sale
from app.plain.figures import WORDS
from tests.test_import_run import checked
from tests.test_import_run import run as run_import
from tests.test_import_run import scoped as scoped_import
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped
from tests.test_manual_records import call, create

CODES = {"money_owed", "overdue_invoices", "days_to_get_paid"}
SALE = {"sold_on": "2026-09-10", "amount": "120.00", "amount_includes_vat": True, "vat_rate": "20"}


# --- typing an invoice in ----------------------------------------------------------------------


def test_a_sale_can_be_an_invoice_with_a_due_day(api, business):
    made = create(api, business, "sales", {**SALE, "due_on": "2026-10-10"})
    assert made["due_on"] == "2026-10-10" and made["paid_on"] is None


def test_an_ordinary_sale_is_not_an_invoice(api, business):
    made = create(api, business, "sales", SALE)
    assert made["due_on"] is None and made["paid_on"] is None


def test_it_cannot_be_due_before_the_day_of_the_sale(api, business):
    res = call(api, business, "POST", "sales", {**SALE, "due_on": "2026-09-01"})
    assert res.status_code == 422


def test_it_can_be_marked_paid_later_and_changed_back(api, business):
    made = create(api, business, "sales", {**SALE, "due_on": "2026-10-10"})
    paid = call(api, business, "PATCH", f"sales/{made['id']}", {"paid_on": "2026-09-20"}).json()
    assert paid["paid_on"] == "2026-09-20" and paid["due_on"] == "2026-10-10"
    back = call(api, business, "PATCH", f"sales/{made['id']}", {"paid_on": None}).json()
    assert back["paid_on"] is None


def test_moving_the_sale_after_its_due_day_is_refused(api, business):
    made = create(api, business, "sales", {**SALE, "due_on": "2026-09-20"})
    res = call(api, business, "PATCH", f"sales/{made['id']}", {"sold_on": "2026-09-25"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "due_before_sale"


# --- the figures, worked out by hand -------------------------------------------------------------


def invoice(db, business, sold, gross, due, paid=None):
    with scoped(db, business):
        net = D(gross) / D("1.2")
        db.add(
            Sale(
                sold_on=sold,
                kind="sale",
                net_amount=net,
                vat_amount=D(gross) - net,
                gross_amount=D(gross),
                due_on=due,
                paid_on=paid,
            )
        )
        db.flush()


@pytest.fixture
def ledger(db, business):
    """A: sold 1 Aug, £120, due 15 Aug, paid 20 Aug (19 days). B: sold 5 Aug, £240, due 5 Sep, unpaid.
    C: sold 10 Aug, £60, due 20 Aug, unpaid. D: an ordinary sale of £500, not an invoice."""
    invoice(db, business, date(2026, 8, 1), 120, date(2026, 8, 15), date(2026, 8, 20))
    invoice(db, business, date(2026, 8, 5), 240, date(2026, 9, 5))
    invoice(db, business, date(2026, 8, 10), 60, date(2026, 8, 20))
    with scoped(db, business):
        db.add(
            Sale(
                sold_on=date(2026, 8, 12),
                kind="sale",
                net_amount=D("416.67"),
                vat_amount=D("83.33"),
                gross_amount=D(500),
            )
        )
        db.flush()
    run(db, business, first=date(2026, 8, 1), today=date(2026, 11, 15))
    return business


def test_what_is_owed_at_the_end_of_each_month(api, ledger):
    assert (
        value(api, ledger, "money_owed", "2026-08-01") == "300.00"
    )  # B and C (A was paid on the 20th)
    assert value(api, ledger, "money_owed", "2026-09-01") == "300.00"


def test_what_is_overdue_is_what_is_past_its_due_day_at_the_end(api, ledger):
    assert (
        value(api, ledger, "overdue_invoices", "2026-08-01") == "60.00"
    )  # only C: B is not due until 5 Sep
    assert value(api, ledger, "overdue_invoices", "2026-09-01") == "300.00"


def test_how_long_customers_take_to_pay(api, ledger):
    assert value(api, ledger, "days_to_get_paid", "2026-08-01") == "19"


def test_a_refund_is_never_counted_as_owed(db, api, business):
    invoice(db, business, date(2026, 8, 1), 120, date(2026, 8, 15))
    with scoped(db, business):
        db.add(
            Sale(
                sold_on=date(2026, 8, 3),
                kind="refund",
                net_amount=D("-50"),
                vat_amount=D("-10"),
                gross_amount=D("-60"),
                due_on=date(2026, 8, 20),
            )
        )
        db.flush()
    run(db, business, first=date(2026, 8, 1), today=date(2026, 11, 15))
    assert value(api, business, "money_owed", "2026-08-01") == "120.00"


# --- who sees them -------------------------------------------------------------------------------


def codes(api, business):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"])
    return {k["code"] for k in res.json()["kpis"]}


def test_a_business_that_takes_the_money_at_the_till_is_not_shown_them(api, db, business):
    with scoped(db, business):
        db.add(
            Sale(
                sold_on=date(2026, 8, 12),
                kind="sale",
                net_amount=D(10),
                vat_amount=D(2),
                gross_amount=D(12),
            )
        )
        db.flush()
    assert not CODES & codes(api, business)


def test_a_business_that_sends_invoices_is_shown_them(api, db, business):
    invoice(db, business, date(2026, 8, 1), 120, date(2026, 8, 15))
    assert CODES <= codes(api, business)


def test_every_figure_has_plain_words_and_claims_no_cause():
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert (
                say.says.strip()
                and say.why.strip()
                and "because" not in (say.says + say.why).lower()
            )


# --- from a file ---------------------------------------------------------------------------------


def test_the_due_day_and_the_paid_day_come_in_from_a_file(api, db, business):
    mapping = {
        "sold_on": "Date",
        "amount": "Total",
        "vat_amount": "VAT",
        "reference": "Invoice",
        "due_on": "Due",
        "paid_on": "Paid",
    }
    content = b"Date,Invoice,Total,VAT,Due,Paid\n01/08/2026,I1,120.00,20.00,15/08/2026,20/08/2026\n05/08/2026,I2,240.00,40.00,05/09/2026,\n"
    res = run_import(
        api, business, checked(api, business, content, mapping, {"vat_inclusive": True}, "sales")
    )
    assert res.status_code == 200, res.text
    with scoped_import(db, business):
        from sqlalchemy import select

        rows = db.scalars(select(Sale).order_by(Sale.sold_on)).all()
        assert [(r.due_on, r.paid_on) for r in rows] == [
            (date(2026, 8, 15), date(2026, 8, 20)),
            (date(2026, 9, 5), None),
        ]


def test_a_file_with_a_due_day_before_the_sale_has_that_row_refused(api, business):
    from app.services.import_rows import parse_row

    out = parse_row(
        "sales",
        {"Date": "10/08/2026", "Total": "120.00", "VAT": "20.00", "Due": "01/08/2026"},
        {"sold_on": "Date", "amount": "Total", "vat_amount": "VAT", "due_on": "Due"},
        {"vat_inclusive": True},
        today=date(2026, 10, 1),
    )
    assert not out.ok and out.errors[0]["code"] == "due_before_sale"
