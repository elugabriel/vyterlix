"""Judging one row of an upload: the money arithmetic and each dataset's rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.import_rows import parse_row

D = Decimal
TODAY = date(2026, 9, 30)

SALES_MAP = {
    "sold_on": "Date",
    "amount": "Total",
    "vat_amount": "VAT",
    "reference": "Ref",
    "product": "Product",
    "quantity": "Qty",
    "cost": "Cost",
    "discount": "Discount",
    "customer_email": "Email",
    "customer_postcode": "Postcode",
    "channel": "Channel",
}
INC = {"vat_inclusive": True}
EXC = {"vat_inclusive": False}


def sale(raw, mapping=SALES_MAP, options=INC):
    base = {"Date": "28/09/2026", "Total": "12.00", "VAT": "2.00"}
    return parse_row("sales", base | raw, mapping, options, today=TODAY)


def codes(outcome):
    return [e["code"] for e in outcome.errors]


def money(outcome):
    v = outcome.values
    return v["net_amount"], v["vat_amount"], v["gross_amount"]


# --- the VAT arithmetic ----------------------------------------


def test_inclusive_amount_with_a_vat_column_the_total_is_gross():
    out = sale({"Total": "£12.00", "VAT": "£2.00"})
    assert out.ok and money(out) == (D("10.00"), D("2.00"), D("12.00"))
    assert out.values["kind"] == "sale"
    assert out.values["sold_on"] == date(2026, 9, 28)


def test_exclusive_amount_with_a_vat_column_the_total_is_net():
    out = sale({"Total": "10.00", "VAT": "2.00"}, options=EXC)
    assert money(out) == (D("10.00"), D("2.00"), D("12.00"))


def test_inclusive_amount_with_a_default_rate_splits_the_total():
    mapping = {"sold_on": "Date", "amount": "Total"}
    out = sale({"Total": "12.00"}, mapping, {"vat_inclusive": True, "default_vat_rate": "20"})
    assert money(out) == (D("10.00"), D("2.00"), D("12.00"))


def test_exclusive_amount_with_a_default_rate_adds_vat():
    mapping = {"sold_on": "Date", "amount": "Total"}
    out = sale({"Total": "10.00"}, mapping, {"vat_inclusive": False, "default_vat_rate": "20"})
    assert money(out) == (D("10.00"), D("2.00"), D("12.00"))


@pytest.mark.parametrize(
    ("total", "rate", "inclusive", "net", "vat", "gross"),
    [
        ("9.99", "20", True, "8.33", "1.66", "9.99"),  # 9.99 / 1.2 = 8.325 -> 8.33, VAT is the rest
        ("1.00", "20", True, "0.83", "0.17", "1.00"),
        ("10.00", "5", True, "9.52", "0.48", "10.00"),
        ("10.00", "0", True, "10.00", "0.00", "10.00"),
        ("8.33", "20", False, "8.33", "1.67", "10.00"),  # 1.666 -> 1.67
        ("0.10", "20", False, "0.10", "0.02", "0.12"),
        ("100", "5", False, "100", "5.00", "105.00"),
    ],
)
def test_vat_is_split_to_the_penny_and_always_adds_up(total, rate, inclusive, net, vat, gross):
    mapping = {"sold_on": "Date", "amount": "Total"}
    out = sale({"Total": total}, mapping, {"vat_inclusive": inclusive, "default_vat_rate": rate})
    assert money(out) == (D(net), D(vat), D(gross))
    n, v, g = money(out)
    assert n + v == g


def test_a_rate_column_beats_the_default_rate():
    mapping = {"sold_on": "Date", "amount": "Total", "vat_rate": "Rate"}
    options = {"vat_inclusive": True, "default_vat_rate": "20"}
    out = sale({"Total": "10.50", "Rate": "5%"}, mapping, options)
    assert money(out) == (D("10.00"), D("0.50"), D("10.50"))


def test_a_bad_rate_in_the_rate_column_is_a_problem_not_a_fallback():
    mapping = {"sold_on": "Date", "amount": "Total", "vat_rate": "Rate"}
    out = sale({"Rate": "17.5"}, mapping, {"vat_inclusive": True, "default_vat_rate": "20"})
    assert codes(out) == ["invalid_vat_rate"]


def test_a_blank_vat_cell_falls_back_to_the_default_rate():
    options = {"vat_inclusive": True, "default_vat_rate": "20"}
    assert money(sale({"VAT": ""}, options=options)) == (D("10.00"), D("2.00"), D("12.00"))


def test_a_blank_vat_cell_with_no_default_is_a_problem():
    assert codes(sale({"VAT": ""})) == ["vat_missing"]


def test_vat_larger_than_the_total_is_a_problem():
    assert codes(sale({"Total": "1.00", "VAT": "2.00"})) == ["vat_exceeds_amount"]


def test_a_zero_vat_cell_means_no_vat():
    out = sale({"Total": "30.00", "VAT": "0"})
    assert money(out) == (D("30.00"), D("0"), D("30.00"))


# --- refunds ----------------------------------------


def test_a_negative_amount_is_a_refund_with_negative_vat():
    out = sale({"Total": "-£12.00", "VAT": "2.00"})
    assert out.values["kind"] == "refund"
    assert money(out) == (D("-10.00"), D("-2.00"), D("-12.00"))


def test_refunds_given_with_negative_vat_too():
    out = sale({"Total": "(12.00)", "VAT": "(2.00)"})
    assert money(out) == (D("-10.00"), D("-2.00"), D("-12.00"))


def test_a_refund_split_by_rate():
    mapping = {"sold_on": "Date", "amount": "Total"}
    out = sale({"Total": "-12.00"}, mapping, {"vat_inclusive": True, "default_vat_rate": "20"})
    assert money(out) == (D("-10.00"), D("-2.00"), D("-12.00"))


def test_a_zero_amount_has_nothing_to_record():
    assert codes(sale({"Total": "0.00"})) == ["zero_amount"]


# --- the rest of a sales row ----------------------------------------


def test_optional_sales_columns_are_read():
    out = sale(
        {
            "Ref": " INV-7 ",
            "Product": "Sourdough  loaf",
            "Qty": "2",
            "Cost": "£3.10",
            "Discount": "-1.00",  # exports often show discounts as negatives
            "Email": "Jo@Example.com",
            "Postcode": "ls1 4ap",
            "Channel": "Shop",
        }
    )
    assert out.ok, out.errors
    v = out.values
    assert (v["reference"], v["product"], v["quantity"]) == ("INV-7", "Sourdough loaf", D("2"))
    assert (v["cost"], v["discount"]) == (D("3.10"), D("1.00"))
    assert (v["customer_email"], v["customer_postcode"], v["channel"]) == (
        "jo@example.com",
        "LS1 4AP",
        "Shop",
    )
    assert out.reference == "INV-7" and out.key == ("ref", "INV-7")


def test_blank_optional_cells_are_simply_missing_not_errors():
    out = sale({"Ref": "", "Product": "", "Qty": "", "Cost": "", "Email": ""})
    assert out.ok and out.key is None and out.values["cost"] is None
    assert out.values["quantity"] is None and out.values["customer_email"] is None


def test_unmapped_columns_are_never_looked_at():
    out = sale(
        {"Surprise": "not a number"},
        mapping={"sold_on": "Date", "amount": "Total", "vat_amount": "VAT"},
    )
    assert out.ok


def test_every_problem_in_a_row_is_reported_together():
    out = sale({"Date": "31/04/2026", "Total": "abc", "Email": "nope", "Postcode": "90210"})
    assert sorted(e["field"] for e in out.errors) == [
        "amount",
        "customer_email",
        "customer_postcode",
        "sold_on",
    ]


def test_required_cells_must_not_be_blank():
    out = sale({"Date": "", "Total": ""})
    assert codes(out) == ["missing_value", "missing_value"]
    assert out.errors[0]["message"] == "Date of sale is blank."


def test_a_zero_quantity_is_a_problem():
    assert codes(sale({"Qty": "0"})) == ["zero_quantity"]


def test_a_row_with_problems_has_no_values_to_import():
    assert sale({"Date": "nonsense"}).values == {}


def test_errors_name_the_field_the_code_and_a_message():
    [error] = sale({"Date": "99/99/2026"}).errors
    assert set(error) == {"field", "code", "message"}
    assert error["field"] == "sold_on" and "99/99/2026" in error["message"]


def test_future_dates_are_problems():
    assert codes(sale({"Date": "28/09/2099"})) == ["date_out_of_range"]


def test_long_text_is_a_problem():
    assert codes(sale({"Channel": "x" * 101})) == ["too_long"]


# --- expenses ----------------------------------------

EXP_MAP = {
    "spent_on": "Date",
    "amount": "Net",
    "vat_amount": "VAT",
    "supplier": "Supplier",
    "category": "Category",
    "reference": "Invoice",
}


def expense(raw, options=EXC):
    base = {"Date": "03/09/2026", "Net": "50.00", "VAT": "10.00"}
    return parse_row("expenses", base | raw, EXP_MAP, options, today=TODAY)


def test_an_expense():
    out = expense({"Supplier": "Flour Co", "Category": "Stock", "Invoice": "F-100"})
    assert out.ok and out.values["kind"] == "expense"
    assert money(out) == (D("50.00"), D("10.00"), D("60.00"))
    assert out.values["spent_on"] == date(2026, 9, 3)
    assert (out.values["supplier"], out.values["category"]) == ("Flour Co", "Stock")
    assert out.key == ("ref", "F-100")


def test_a_credit_note_is_a_negative_expense():
    out = expense({"Net": "-50.00", "VAT": "10.00"})
    assert out.values["kind"] == "credit"
    assert money(out) == (D("-50.00"), D("-10.00"), D("-60.00"))


# --- customers ----------------------------------------

CUST_MAP = {"name": "Name", "email": "Email", "postcode": "Postcode", "reference": "ID"}


def customer(raw):
    return parse_row("customers", raw, CUST_MAP, {}, today=TODAY)


def test_a_customer():
    out = customer({"Name": "Jo Bloggs", "Email": "JO@x.co.uk", "Postcode": "ls14ap", "ID": "C1"})
    assert out.ok and out.values["email"] == "jo@x.co.uk" and out.values["postcode"] == "LS1 4AP"
    assert out.key == ("ref", "C1")


def test_a_customer_is_recognised_by_email_without_a_reference():
    assert customer({"Name": "Jo", "Email": "jo@x.co.uk"}).key == ("email", "jo@x.co.uk")
    assert customer({"Name": "Jo"}).key is None


def test_a_customer_needs_a_name_or_an_email():
    assert codes(customer({"Name": "", "Email": ""})) == ["empty_row"]
    assert customer({"Name": "", "Email": "a@b.co.uk"}).ok
    assert customer({"Name": "Jo", "Email": ""}).ok


def test_a_bad_customer_email_is_reported_once():
    assert codes(customer({"Name": "Jo", "Email": "nope"})) == ["invalid_email"]


# --- suppliers and products ----------------------------------------


def test_a_supplier_needs_a_name():
    ok = parse_row("suppliers", {"N": " Flour Co "}, {"name": "N"}, {}, today=TODAY)
    assert ok.ok and ok.key == ("name", "flour co")
    bad = parse_row("suppliers", {"N": ""}, {"name": "N"}, {}, today=TODAY)
    assert codes(bad) == ["missing_value"] and bad.key is None


PROD_MAP = {
    "name": "Name",
    "sku": "SKU",
    "unit_price": "Price",
    "unit_cost": "Cost",
    "vat_rate": "VAT",
}


def product(raw, options=None):
    return parse_row("products", raw, PROD_MAP, options or {"vat_inclusive": True}, today=TODAY)


def test_a_product_price_including_vat_is_stored_without_it():
    out = product({"Name": "Loaf", "SKU": "SD1", "Price": "£6.00", "Cost": "2.10", "VAT": "20"})
    assert out.ok
    assert out.values["unit_price_ex_vat"] == D("5.0000") and out.values["vat_rate"] == D("20")
    assert out.values["unit_cost"] == D("2.10")
    assert out.key == ("sku", "SD1")


def test_a_product_price_excluding_vat_is_kept_as_is():
    out = product({"Name": "Loaf", "SKU": "", "Price": "5.00", "Cost": "", "VAT": "20"}, EXC)
    assert out.values["unit_price_ex_vat"] == D("5.00")
    assert out.key == ("name", "loaf")


def test_a_product_without_a_rate_uses_the_files_default_rate():
    options = {"vat_inclusive": True, "default_vat_rate": "5"}
    out = product({"Name": "Pie", "SKU": "", "Price": "10.50", "Cost": "", "VAT": ""}, options)
    assert out.values["unit_price_ex_vat"] == D("10.0000") and out.values["vat_rate"] == D("5")


def test_a_product_price_including_vat_with_no_rate_anywhere_is_a_problem():
    out = product({"Name": "Pie", "SKU": "", "Price": "10.50", "Cost": "", "VAT": ""})
    assert codes(out) == ["vat_missing"]


def test_product_prices_and_costs_cannot_be_negative():
    assert codes(product({"Name": "A", "SKU": "", "Price": "-1", "Cost": "", "VAT": "20"})) == [
        "negative_amount"
    ]
    assert codes(product({"Name": "A", "SKU": "", "Price": "", "Cost": "-1", "VAT": ""})) == [
        "negative_amount"
    ]


# --- stock movements ----------------------------------------

STOCK_MAP = {
    "moved_on": "Date",
    "sku": "SKU",
    "product": "Item",
    "quantity": "Qty",
    "movement_kind": "Type",
    "unit_cost": "Cost",
}


def stock(raw):
    base = {"Date": "01/09/2026", "SKU": "SD1", "Item": "", "Qty": "10", "Type": "", "Cost": ""}
    return parse_row("stock_movements", base | raw, STOCK_MAP, {}, today=TODAY)


def test_a_delivery():
    out = stock({"Type": "Delivery", "Cost": "£2.50"})
    assert out.ok and out.values["kind"] == "delivery" and out.values["quantity"] == D("10")
    assert out.values["unit_cost"] == D("2.50")


def test_the_type_of_movement_is_worked_out_from_the_sign_when_not_given():
    assert stock({"Qty": "5"}).values["kind"] == "delivery"
    assert stock({"Qty": "-5"}).values["kind"] == "adjustment"


@pytest.mark.parametrize(
    ("kind", "qty", "ok"),
    [
        ("opening", "40", True),
        ("opening", "0", True),
        ("opening", "-1", False),
        ("delivery", "12", True),
        ("delivery", "-12", False),
        ("return", "1", True),
        ("sale", "-2", True),
        ("sale", "2", False),
        ("write off", "-3", True),
        ("Write-Off", "3", False),
        ("adjustment", "-1", True),
        ("adjustment", "1", True),
    ],
)
def test_the_direction_of_a_movement_must_match_its_type(kind, qty, ok):
    out = stock({"Type": kind, "Qty": qty})
    assert out.ok is ok, out.errors
    if not ok:
        assert codes(out) == ["sign_mismatch"]


def test_an_unknown_type_of_movement_is_a_problem():
    assert codes(stock({"Type": "teleport"})) == ["invalid_movement_kind"]


def test_a_stock_movement_needs_a_product_or_a_code():
    assert stock({"SKU": "", "Item": "Loaf"}).ok
    assert stock({"SKU": "SD1", "Item": ""}).ok
    out = stock({"SKU": "", "Item": ""})
    assert codes(out) == ["missing_value"] and out.errors[0]["field"] == "product"


def test_a_zero_stock_quantity_is_a_problem():
    assert codes(stock({"Qty": "0", "Type": ""})) == ["zero_quantity"]
