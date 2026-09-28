"""Phase 4 trading data: the database itself enforces money, UK and separation rules."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import TenantScopeError, tenant_scope
from app.models.business import BusinessListItem
from app.models.data import (
    Customer,
    Expense,
    Product,
    Sale,
    SaleLine,
    StockMovement,
    Supplier,
)
from app.models.identity import Organization

D = Decimal
TODAY = date(2026, 9, 28)


@pytest.fixture
def orgs(db):
    a, b = Organization(name="A Ltd"), Organization(name="B Ltd")
    db.add_all([a, b])
    db.flush()
    return a.id, b.id


@pytest.fixture
def org_a(db, orgs):
    with tenant_scope(db, orgs[0]):
        yield orgs[0]


def saved(db, obj):
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def rejected(db, obj, constraint):
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        db.add(obj)
        db.flush()


def sale(**kw):
    fields = {
        "sold_on": TODAY,
        "net_amount": D("100.00"),
        "vat_amount": D("20.00"),
        "gross_amount": D("120.00"),
    }
    return Sale(**(fields | kw))


def expense(**kw):
    fields = {
        "spent_on": TODAY,
        "net_amount": D("50.00"),
        "vat_amount": D("10.00"),
        "gross_amount": D("60.00"),
    }
    return Expense(**(fields | kw))


# --- separation between businesses ----------------------------------------------------------


@pytest.mark.parametrize(
    "model", [Customer, Supplier, Product, Sale, SaleLine, Expense, StockMovement]
)
def test_trading_tables_need_a_business_in_scope(db, model):
    with pytest.raises(TenantScopeError):
        db.execute(select(model))


def test_a_sale_cannot_point_at_another_businesss_customer(db, orgs):
    a, b = orgs
    with tenant_scope(db, b):
        their_customer = saved(db, Customer(name="B's customer"))
    with tenant_scope(db, a):
        rejected(db, sale(customer_id=their_customer.id), "fk_sales_organization_id_customers")


def test_a_sale_cannot_use_another_businesss_sales_channel(db, orgs):
    a, b = orgs
    with tenant_scope(db, b):
        their_channel = saved(db, BusinessListItem(kind="sales_channel", name="Their shop"))
    with tenant_scope(db, a):
        rejected(
            db,
            sale(sales_channel_id=their_channel.id),
            "fk_sales_organization_id_business_list_items",
        )


def test_an_expense_cannot_use_another_businesss_supplier(db, orgs):
    a, b = orgs
    with tenant_scope(db, b):
        their_supplier = saved(db, Supplier(name="B's supplier"))
    with tenant_scope(db, a):
        rejected(
            db, expense(supplier_id=their_supplier.id), "fk_expenses_organization_id_suppliers"
        )


def test_links_within_the_same_business_work(db, org_a):
    customer = saved(db, Customer(name="Jo Bloggs", postcode="LS1 4AP"))
    channel = saved(db, BusinessListItem(kind="sales_channel", name="Shop"))
    product = saved(db, Product(name="Sourdough loaf", sku="SD-800", unit_price_ex_vat=D("4.50")))
    s = saved(db, sale(customer_id=customer.id, sales_channel_id=channel.id))
    line = saved(
        db, SaleLine(sale_id=s.id, product_id=product.id, quantity=D("2"), net_amount=D("9.00"))
    )
    assert line.organization_id == org_a


# --- money --------------------------------------------------------------------------------------


def test_amounts_are_exact_pounds_and_pence(db, org_a):
    s = saved(db, sale(net_amount=D("0.10"), vat_amount=D("0.02"), gross_amount=D("0.12")))
    assert (s.net_amount, s.vat_amount, s.gross_amount) == (D("0.1000"), D("0.0200"), D("0.1200"))


def test_gross_must_equal_net_plus_vat(db, org_a):
    rejected(db, sale(gross_amount=D("119.99")), "gross_is_net_plus_vat")
    rejected(db, expense(gross_amount=D("61.00")), "gross_is_net_plus_vat")


def test_only_pounds_sterling(db, org_a):
    rejected(db, sale(currency="USD"), "currency_gbp")
    rejected(db, expense(currency="EUR"), "currency_gbp")


def test_refunds_are_negative_and_sales_positive(db, org_a):
    saved(db, sale(kind="refund", net_amount=D("-10"), vat_amount=D("-2"), gross_amount=D("-12")))
    rejected(
        db,
        sale(net_amount=D("-10"), vat_amount=D("-2"), gross_amount=D("-12")),
        "sign_matches_kind",
    )
    rejected(db, sale(kind="refund"), "sign_matches_kind")
    rejected(db, sale(discount_amount=D("-1")), "discount_not_negative")


def test_expense_credits_are_negative(db, org_a):
    saved(db, expense(kind="credit", net_amount=D("-5"), vat_amount=D("-1"), gross_amount=D("-6")))
    rejected(db, expense(kind="credit"), "sign_matches_kind")


def test_vat_free_sales_are_fine(db, org_a):
    # e.g. most food, children's clothes, books: zero-rated
    s = saved(db, sale(net_amount=D("30"), vat_amount=D("0"), gross_amount=D("30")))
    assert s.vat_amount == 0


@pytest.mark.parametrize("rate", ["17.5", "15", "-5"])
def test_only_uk_vat_rates(db, org_a, rate):
    rejected(db, Product(name="Thing", vat_rate=D(rate)), "vat_rate_uk")


@pytest.mark.parametrize("rate", ["20", "5", "0", None])
def test_uk_vat_rates_accepted(db, org_a, rate):
    saved(db, Product(name="Thing", vat_rate=D(rate) if rate else None))


def test_unknown_cost_of_goods_is_allowed_but_never_negative(db, org_a):
    s = saved(db, sale())
    line = saved(db, SaleLine(sale_id=s.id, quantity=D("1"), net_amount=D("100")))
    assert line.cost_amount is None  # a data gap to report, never a guess
    rejected(
        db,
        SaleLine(sale_id=s.id, quantity=D("1"), net_amount=D("1"), cost_amount=D("-1")),
        "cost_not_negative",
    )
    rejected(db, SaleLine(sale_id=s.id, quantity=D("0"), net_amount=D("0")), "quantity_not_zero")


# --- where records came from ------------------------------------------------------------------


def test_same_record_from_the_same_source_only_once(db, org_a):
    saved(db, sale(source="shopify", source_ref="#1001"))
    rejected(db, sale(source="shopify", source_ref="#1001"), "uq_sales_source_ref")


def test_same_reference_from_a_different_source_is_a_different_record(db, org_a):
    saved(db, sale(source="shopify", source_ref="1001"))
    saved(db, sale(source="woocommerce", source_ref="1001"))


def test_records_without_a_reference_are_never_treated_as_duplicates(db, org_a):
    saved(db, sale())
    saved(db, sale())  # two identical manual cash sales on the same day are fine


def test_unknown_source_rejected(db, org_a):
    rejected(db, sale(source="carrier_pigeon"), "source_valid")


def test_another_business_can_use_the_same_reference(db, orgs):
    a, b = orgs
    with tenant_scope(db, a):
        saved(db, sale(source="csv", source_ref="INV-1"))
    with tenant_scope(db, b):
        saved(db, sale(source="csv", source_ref="INV-1"))


# --- products, customers, stock -----------------------------------------------------------------


def test_sku_unique_within_a_business_only(db, orgs):
    a, b = orgs
    with tenant_scope(db, a):
        saved(db, Product(name="Loaf", sku="SD-800"))
        rejected(db, Product(name="Other loaf", sku="SD-800"), "uq_products_sku")
    with tenant_scope(db, b):
        saved(db, Product(name="Their loaf", sku="SD-800"))


def test_customer_details_are_optional_but_uk_shaped(db, org_a):
    saved(db, Customer())  # e.g. an anonymous walk-in customer
    rejected(db, Customer(postcode="90210"), "postcode_format")
    rejected(db, Customer(email="Jo@Example.com"), "email_lowercase")


@pytest.mark.parametrize(
    ("kind", "quantity", "ok"),
    [
        ("opening", "40", True),
        ("delivery", "12", True),
        ("sale", "-2", True),
        ("return", "1", True),
        ("write_off", "-3", True),
        ("adjustment", "-1", True),
        ("delivery", "-12", False),
        ("sale", "2", False),
        ("write_off", "3", False),
        ("adjustment", "0", False),
    ],
)
def test_stock_movement_direction_matches_its_kind(db, org_a, kind, quantity, ok):
    product = saved(db, Product(name="Croissant"))
    move = StockMovement(product_id=product.id, moved_on=TODAY, kind=kind, quantity=D(quantity))
    if ok:
        saved(db, move)
    else:
        rejected(db, move, "sign_matches_kind")


def test_current_stock_is_the_sum_of_movements(db, org_a):
    product = saved(db, Product(name="Croissant"))
    for kind, qty in [("opening", "40"), ("delivery", "24"), ("sale", "-30"), ("write_off", "-4")]:
        saved(db, StockMovement(product_id=product.id, moved_on=TODAY, kind=kind, quantity=D(qty)))
    level = db.scalar(
        select(func.sum(StockMovement.quantity)).where(StockMovement.product_id == product.id)
    )
    assert level == D("30")


# --- deleting -----------------------------------------------------------------------------------


def test_a_customer_with_sales_cannot_be_deleted(db, org_a):
    customer = saved(db, Customer(name="Jo"))
    saved(db, sale(customer_id=customer.id))
    with (
        pytest.raises(IntegrityError, match="fk_sales_organization_id_customers"),
        db.begin_nested(),
    ):
        db.execute(delete(Customer).where(Customer.id == customer.id))


def test_deleting_a_sale_removes_its_lines(db, org_a):
    s = saved(db, sale())
    saved(db, SaleLine(sale_id=s.id, quantity=D("1"), net_amount=D("100")))
    db.execute(delete(Sale).where(Sale.id == s.id))
    assert db.scalar(select(func.count()).select_from(SaleLine)) == 0
