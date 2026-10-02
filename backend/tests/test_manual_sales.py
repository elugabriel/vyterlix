"""Typing sales and expenses in by hand: POST/GET/PATCH/DELETE under /sales and /expenses."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.uk import today_uk
from app.db.tenant import tenant_scope
from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, Supplier
from app.models.identity import AuditLog, OrganizationUser, Role, User

ORGS = "/api/v1/organizations"
D = Decimal
SALE = {"sold_on": "2026-09-28", "amount": "12.00", "amount_includes_vat": True, "vat_rate": "20"}
EXPENSE = {
    "spent_on": "2026-09-03",
    "amount": "60.00",
    "amount_includes_vat": True,
    "vat_rate": "20",
}
MONEY = {"amount": "12.00", "amount_includes_vat": True, "vat_rate": "20"}


@pytest.fixture
def business(api, db, signup):
    auth = {
        "owner": signup("owner@acme.co.uk"),
        "viewer": signup("viewer@acme.co.uk"),
        "other": signup("owner@rival.co.uk"),
    }
    org_id = api.post(ORGS, json={"name": "Acme"}, headers=auth["owner"]).json()["id"]
    other_org = api.post(ORGS, json={"name": "Rival"}, headers=auth["other"]).json()["id"]
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(org_id),
            user_id=db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "viewer")).first(),
        )
    )
    db.flush()
    return org_id, other_org, auth


def call(api, business, method, path, body=None, who="owner", org=0, **params):
    url = f"{ORGS}/{business[org]}/{path}"
    return api.request(method, url, json=body, params=params or None, headers=business[2][who])


def sale(api, business, **fields):
    res = call(api, business, "POST", "sales", SALE | fields)
    assert res.status_code == 201, res.text
    return res.json()


def expense(api, business, **fields):
    res = call(api, business, "POST", "expenses", EXPENSE | fields)
    assert res.status_code == 201, res.text
    return res.json()


def patch(api, business, kind, record_id, body, **kw):
    return call(api, business, "PATCH", f"{kind}/{record_id}", body, **kw)


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def make(db, business, obj, org=0):
    with scoped(db, business, org):
        db.add(obj)
        db.flush()
        return str(obj.id)


def list_item(db, business, kind, name, *, active=True, org=0):
    return make(db, business, BusinessListItem(kind=kind, name=name, is_active=active), org)


def customer(db, business, name="Jo", org=0):
    return make(db, business, Customer(name=name), org)


def product(db, business, name="Loaf", org=0):
    return make(db, business, Product(name=name), org)


def line(description="Loaf", quantity="1", net="10.00", **extra):
    return {"description": description, "quantity": quantity, "net_amount": net, **extra}


def amounts(body):
    return body["net_amount"], body["vat_amount"], body["gross_amount"]


# --- creating a sale -----------------------------------------------------------------------------


def test_a_sale_including_vat(api, db, business):
    body = sale(api, business, reference="INV-1", notes="Cash", discount="1.50")
    assert amounts(body) == ("10.00", "2.00", "12.00")
    assert (body["kind"], body["currency"], body["source"]) == ("sale", "GBP", "manual")
    assert body["import_id"] is None and body["reference"] == "INV-1" and body["notes"] == "Cash"
    assert body["discount_amount"] == "1.50" and body["sold_on"] == "2026-09-28"
    assert body["lines"] == []
    with scoped(db, business):
        stored = db.scalars(select(Sale)).one()
        assert (stored.source, stored.source_ref, stored.import_id) == ("manual", "INV-1", None)


def test_a_sale_excluding_vat(api, business):
    body = sale(api, business, amount_includes_vat=False)
    assert amounts(body) == ("12.00", "2.40", "14.40")


def test_a_sale_with_the_vat_amount_given(api, business):
    body = sale(api, business, vat_rate=None, vat_amount="1.50")
    assert amounts(body) == ("10.50", "1.50", "12.00")


@pytest.mark.parametrize("rate", ["20", "5", "0"])
def test_every_uk_vat_rate(api, business, rate):
    body = sale(api, business, vat_rate=rate, amount="100.00")
    assert D(body["net_amount"]) + D(body["vat_amount"]) == D(body["gross_amount"]) == D("100.00")


def test_a_refund_is_stored_negative(api, business):
    body = sale(api, business, kind="refund")
    assert amounts(body) == ("-10.00", "-2.00", "-12.00")


def test_a_sale_can_be_zero_rated(api, business):
    body = sale(api, business, vat_rate="0", amount="30.00")
    assert (body["net_amount"], body["vat_amount"]) == ("30.00", "0.00")


@pytest.mark.parametrize(
    "change",
    [
        {"vat_rate": None},  # neither
        {"vat_amount": "2.00"},  # both
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": "12.005"},
        {"amount": "abc"},
        {"amount": "1000000000000"},
        {"vat_rate": "17.5"},
        {"vat_rate": "twenty"},
        {"sold_on": "2026-04-31"},
        {"sold_on": "28/09/2026"},
        {"sold_on": "2099-01-01"},
        {"sold_on": "1980-01-01"},
        {"kind": "gift"},
        {"discount": "-1"},
        {"colour": "red"},
        {"reference": ""},
        {"reference": "x" * 201},
    ],
)
def test_bad_sales_are_refused(api, business, change):
    res = call(api, business, "POST", "sales", SALE | change)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"


def test_the_inclusive_question_must_be_answered(api, business):
    body = {k: v for k, v in SALE.items() if k != "amount_includes_vat"}
    assert call(api, business, "POST", "sales", body).status_code == 422


def test_vat_larger_than_the_amount_is_refused(api, business):
    res = call(api, business, "POST", "sales", SALE | {"vat_rate": None, "vat_amount": "20.00"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "vat_exceeds_amount"


def test_tomorrow_is_allowed_for_uk_time_zone_slack(api, business):
    tomorrow = (today_uk() + timedelta(days=1)).isoformat()
    assert sale(api, business, sold_on=tomorrow)["sold_on"] == tomorrow
    day_after = (today_uk() + timedelta(days=2)).isoformat()
    assert call(api, business, "POST", "sales", SALE | {"sold_on": day_after}).status_code == 422


def test_the_customer_and_channel_must_be_your_own(api, db, business):
    mine = customer(db, business)
    channel = list_item(db, business, "sales_channel", "Shop")
    ok = sale(api, business, customer_id=mine, sales_channel_id=channel)
    assert ok["customer_id"] == mine and ok["sales_channel_id"] == channel

    def refused(**fields):
        return call(api, business, "POST", "sales", SALE | fields)

    res = refused(customer_id=customer(db, business, "Not mine", org=1))
    assert res.status_code == 422 and res.json()["error"]["details"]["field"] == "customer_id"
    assert refused(customer_id=str(uuid.uuid4())).status_code == 422
    wrong_kind = list_item(db, business, "cost_category", "Rent")
    res = refused(sales_channel_id=wrong_kind)
    assert res.status_code == 422 and res.json()["error"]["details"]["field"] == "sales_channel_id"
    assert (
        refused(
            sales_channel_id=list_item(db, business, "sales_channel", "Old", active=False)
        ).status_code
        == 422
    )
    assert (
        refused(
            sales_channel_id=list_item(db, business, "sales_channel", "Theirs", org=1)
        ).status_code
        == 422
    )


def test_a_reference_can_be_used_once(api, business):
    sale(api, business, reference="INV-1")
    res = call(api, business, "POST", "sales", SALE | {"reference": "INV-1"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "reference_taken"
    sale(api, business)  # sales without a reference are never clashes
    sale(api, business)
    other = call(api, business, "POST", "sales", SALE | {"reference": "INV-1"}, who="other", org=1)
    assert other.status_code == 201  # another business may use the same one


def test_creating_a_sale_is_audited_without_personal_details(api, db, business):
    body = sale(api, business, kind="refund")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "record.created")).one()
    assert entry.target_type == "sales" and entry.target_id == body["id"]
    assert entry.details == {"kind": "refund"}


# --- sale lines ----------------------------------------------------------------------------------


def test_lines_add_up_to_the_sale(api, db, business):
    loaf = product(db, business)
    lines = [
        {"product_id": loaf, "quantity": "2", "net_amount": "6.00", "cost_amount": "2.40"},
        line("Delivery", net="4.00"),
    ]
    body = sale(api, business, lines=lines)
    by_desc = {x["description"]: x for x in body["lines"]}
    bread = next(x for x in body["lines"] if x["product_id"] == loaf)
    assert (bread["quantity"], bread["net_amount"], bread["cost_amount"]) == (
        "2.0000",
        "6.00",
        "2.40",
    )
    assert bread["unit_price_ex_vat"] == "3.0000"
    assert by_desc["Delivery"]["product_id"] is None and by_desc["Delivery"]["cost_amount"] is None
    assert sum(D(x["net_amount"]) for x in body["lines"]) == D(body["net_amount"])
    assert sum(D(x["vat_amount"]) for x in body["lines"]) == D(body["vat_amount"])


def test_line_vat_is_shared_to_the_penny_and_always_adds_up(api, business):
    lines = [line(f"Item {n}", net="3.33") for n in range(3)] + [line("Last", net="0.00")]
    body = sale(api, business, amount="11.99", vat_rate=None, vat_amount="2.00", lines=lines)
    assert sum(D(x["vat_amount"]) for x in body["lines"]) == D("2.00")
    assert sum(D(x["net_amount"]) for x in body["lines"]) == D(body["net_amount"]) == D("9.99")


def test_refund_lines_are_negative(api, business):
    body = sale(api, business, kind="refund", lines=[line(quantity="2")])
    [only] = body["lines"]
    assert (only["quantity"], only["net_amount"], only["vat_amount"]) == (
        "-2.0000",
        "-10.00",
        "-2.00",
    )
    assert only["unit_price_ex_vat"] == "5.0000"  # a unit price is never negative


def test_lines_that_do_not_add_up_are_refused(api, business):
    res = call(api, business, "POST", "sales", SALE | {"lines": [line(net="9.00")]})
    error = res.json()["error"]
    assert res.status_code == 422 and error["code"] == "lines_do_not_add_up"
    assert error["details"] == {"field": "lines", "expected": "10.00", "got": "9.00"}


@pytest.mark.parametrize(
    "bad_line",
    [
        {"quantity": "1", "net_amount": "10.00"},  # nothing says what was sold
        line(quantity="0"),
        line(quantity="-1"),
        line(net="-10.00"),
        line(cost_amount="-1"),
        line(colour="red"),
    ],
)
def test_bad_lines_are_refused(api, business, bad_line):
    assert call(api, business, "POST", "sales", SALE | {"lines": [bad_line]}).status_code == 422


def test_a_line_must_use_your_own_product(api, db, business):
    theirs = product(db, business, org=1)
    bad = {"product_id": theirs, "quantity": "1", "net_amount": "10.00"}
    res = call(api, business, "POST", "sales", SALE | {"lines": [bad]})
    assert res.status_code == 422 and res.json()["error"]["details"]["field"] == "lines"
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Sale)) == 0  # nothing half-saved


# --- reading -------------------------------------------------------------------------------------


def test_get_one_sale_with_its_lines(api, business):
    made = sale(api, business, lines=[line()])
    assert call(api, business, "GET", f"sales/{made['id']}").json() == made


def test_unknown_and_other_businesss_sales_are_not_found(api, business):
    made = sale(api, business)
    assert call(api, business, "GET", f"sales/{uuid.uuid4()}").status_code == 404
    for org in (0, 1):
        res = call(api, business, "GET", f"sales/{made['id']}", who="other", org=org)
        assert res.status_code == 404


def test_the_list_is_newest_first_and_paged(api, business):
    for day in ("2026-09-01", "2026-09-03", "2026-09-02"):
        sale(api, business, sold_on=day)
    page = call(api, business, "GET", "sales").json()
    assert page["total"] == 3
    assert [s["sold_on"] for s in page["items"]] == ["2026-09-03", "2026-09-02", "2026-09-01"]
    second = call(api, business, "GET", "sales", limit=1, offset=1).json()
    assert second["total"] == 3 and [s["sold_on"] for s in second["items"]] == ["2026-09-02"]
    assert call(api, business, "GET", "sales", offset=10).json()["items"] == []


def test_the_list_includes_lines(api, business):
    sale(api, business, lines=[line()])
    [item] = call(api, business, "GET", "sales").json()["items"]
    assert [x["description"] for x in item["lines"]] == ["Loaf"]


def test_filtering_sales(api, db, business):
    jo, shop = customer(db, business), list_item(db, business, "sales_channel", "Shop")
    sale(api, business, sold_on="2026-09-01", reference="A-1", customer_id=jo)
    sale(api, business, sold_on="2026-09-10", reference="B-2", sales_channel_id=shop)
    sale(api, business, sold_on="2026-09-20", reference="A_3%", kind="refund")

    def refs(**params):
        items = call(api, business, "GET", "sales", **params).json()["items"]
        return sorted(i["reference"] for i in items)

    assert refs(date_from="2026-09-05") == ["A_3%", "B-2"]
    assert refs(date_to="2026-09-10") == ["A-1", "B-2"]
    assert refs(date_from="2026-09-05", date_to="2026-09-15") == ["B-2"]
    assert refs(kind="refund") == ["A_3%"]
    assert refs(customer_id=jo) == ["A-1"]
    assert refs(sales_channel_id=shop) == ["B-2"]
    assert refs(source="manual") == ["A-1", "A_3%", "B-2"] and refs(source="csv") == []
    assert refs(q="a-") == ["A-1"]
    assert refs(q="_3%") == ["A_3%"]  # % and _ are searched for literally
    assert refs(q="%") == ["A_3%"]


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "gift"},
        {"source": "carrier_pigeon"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"date_from": "yesterday"},
        {"customer_id": "nope"},
    ],
)
def test_bad_filters_are_refused(api, business, params):
    assert call(api, business, "GET", "sales", **params).status_code == 422


def test_each_business_lists_only_its_own_sales(api, business):
    sale(api, business, reference="MINE")
    call(api, business, "POST", "sales", SALE | {"reference": "THEIRS"}, who="other", org=1)
    mine = call(api, business, "GET", "sales").json()["items"]
    theirs = call(api, business, "GET", "sales", who="other", org=1).json()["items"]
    assert [s["reference"] for s in mine] == ["MINE"]
    assert [s["reference"] for s in theirs] == ["THEIRS"]


# --- correcting a sale ---------------------------------------------------------------------------


def test_change_the_details_without_touching_the_money(api, db, business):
    jo = customer(db, business)
    made = sale(api, business, reference="INV-1")
    change = {"notes": "Paid", "customer_id": jo, "sold_on": "2026-09-29"}
    res = patch(api, business, "sales", made["id"], change)
    body = res.json()
    assert res.status_code == 200 and body["notes"] == "Paid" and body["customer_id"] == jo
    assert body["sold_on"] == "2026-09-29" and body["gross_amount"] == "12.00"
    assert body["reference"] == "INV-1"


def test_optional_fields_can_be_cleared(api, db, business):
    made = sale(api, business, reference="INV-1", notes="x", customer_id=customer(db, business))
    clear = {"notes": None, "customer_id": None, "reference": None}
    body = patch(api, business, "sales", made["id"], clear).json()
    assert (body["notes"], body["customer_id"], body["reference"]) == (None, None, None)


def test_required_fields_cannot_be_cleared(api, business):
    made = sale(api, business)
    for field in ("sold_on", "kind", "discount"):
        res = patch(api, business, "sales", made["id"], {field: None})
        assert res.status_code == 422


def test_change_the_money(api, business):
    made = sale(api, business)
    money = {"amount": "24.00", "amount_includes_vat": False, "vat_rate": "5"}
    body = patch(api, business, "sales", made["id"], money).json()
    assert amounts(body) == ("24.00", "1.20", "25.20")


def test_the_money_must_be_sent_whole(api, business):
    made = sale(api, business)
    parts = [
        {"amount": "24.00"},
        {"vat_rate": "5"},
        {"amount": "24.00", "amount_includes_vat": True},
        {"amount": "24.00", "amount_includes_vat": True, "vat_rate": "5", "vat_amount": "1"},
    ]
    for part in parts:
        assert patch(api, business, "sales", made["id"], part).status_code == 422


def test_changing_the_kind_needs_the_amount_again(api, business):
    made = sale(api, business)
    res = patch(api, business, "sales", made["id"], {"kind": "refund"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "kind_needs_amount"
    body = patch(api, business, "sales", made["id"], {"kind": "refund"} | MONEY).json()
    assert body["kind"] == "refund" and body["gross_amount"] == "-12.00"
    back = patch(api, business, "sales", made["id"], {"kind": "sale"} | MONEY).json()
    assert back["gross_amount"] == "12.00"


def test_a_refund_stays_a_refund_when_only_the_amount_changes(api, business):
    made = sale(api, business, kind="refund")
    money = {"amount": "6.00", "amount_includes_vat": True, "vat_rate": "20"}
    body = patch(api, business, "sales", made["id"], money).json()
    assert body["kind"] == "refund" and body["gross_amount"] == "-6.00"


def test_a_sale_with_lines_needs_them_sent_again_when_the_money_changes(api, business):
    made = sale(api, business, lines=[line()])
    money = {"amount": "24.00", "amount_includes_vat": True, "vat_rate": "20"}
    res = patch(api, business, "sales", made["id"], money)
    assert res.status_code == 422 and res.json()["error"]["code"] == "lines_need_updating"
    cake = [line("Cake", quantity="2", net="20.00")]
    body = patch(api, business, "sales", made["id"], money | {"lines": cake}).json()
    assert [x["description"] for x in body["lines"]] == ["Cake"] and body["net_amount"] == "20.00"


def test_lines_can_be_replaced_or_removed(api, db, business):
    made = sale(api, business, lines=[line()])
    body = patch(api, business, "sales", made["id"], {"lines": [line("Bun", "5")]}).json()
    assert [x["description"] for x in body["lines"]] == ["Bun"]
    assert patch(api, business, "sales", made["id"], {"lines": []}).json()["lines"] == []
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(SaleLine)) == 0


def test_new_lines_must_still_add_up(api, business):
    made = sale(api, business)
    res = patch(api, business, "sales", made["id"], {"lines": [line(net="1.00")]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "lines_do_not_add_up"


def test_changing_to_a_reference_in_use_is_refused(api, business):
    sale(api, business, reference="A")
    made = sale(api, business, reference="B")
    res = patch(api, business, "sales", made["id"], {"reference": "A"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "reference_taken"
    assert call(api, business, "GET", f"sales/{made['id']}").json()["reference"] == "B"


def test_a_correction_to_an_imported_sale_keeps_its_source(api, db, business):
    imported = Sale(
        sold_on=date(2026, 9, 1),
        net_amount=D("10"),
        vat_amount=D("2"),
        gross_amount=D("12"),
        source="csv",
        source_ref="R1",
    )
    sale_id = make(db, business, imported)
    body = patch(api, business, "sales", sale_id, {"notes": "checked"}).json()
    assert body["source"] == "csv" and body["reference"] == "R1" and body["notes"] == "checked"


def test_patch_rejects_unknown_fields_and_accepts_no_change(api, business):
    made = sale(api, business)
    assert patch(api, business, "sales", made["id"], {"colour": "red"}).status_code == 422
    assert patch(api, business, "sales", made["id"], {}).status_code == 200


def test_updates_are_audited(api, db, business):
    made = sale(api, business)
    patch(api, business, "sales", made["id"], {"notes": "x", "discount": "1"})
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "record.updated")).one()
    assert entry.details == {"fields": ["discount", "notes"]}


# --- removing a sale -----------------------------------------------------------------------------


def test_delete_a_sale_and_its_lines(api, db, business):
    made = sale(api, business, lines=[line()])
    res = call(api, business, "DELETE", f"sales/{made['id']}")
    assert res.status_code == 204 and res.content == b""
    assert call(api, business, "GET", f"sales/{made['id']}").status_code == 404
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Sale)) == 0
        assert db.scalar(select(func.count()).select_from(SaleLine)) == 0
    assert call(api, business, "DELETE", f"sales/{made['id']}").status_code == 404
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "record.deleted")).one()
    assert entry.target_id == made["id"]


def test_another_business_cannot_change_or_delete_a_sale(api, db, business):
    made = sale(api, business)
    for org in (0, 1):
        res = patch(api, business, "sales", made["id"], {"notes": "x"}, who="other", org=org)
        assert res.status_code == 404
        assert (
            call(api, business, "DELETE", f"sales/{made['id']}", who="other", org=org).status_code
            == 404
        )
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Sale)) == 1


# --- expenses ------------------------------------------------------------------------------------


def test_an_expense(api, business):
    body = expense(api, business, description="Flour", reference="F-1")
    assert amounts(body) == ("50.00", "10.00", "60.00")
    assert (body["kind"], body["currency"], body["source"]) == ("expense", "GBP", "manual")
    assert body["import_id"] is None and body["spent_on"] == "2026-09-03"
    assert body["description"] == "Flour" and body["reference"] == "F-1"


def test_a_credit_note_is_negative(api, business):
    body = expense(api, business, kind="credit")
    assert amounts(body) == ("-50.00", "-10.00", "-60.00")


def test_expense_amounts_work_like_sales(api, business):
    given = {
        "amount": "50.00",
        "amount_includes_vat": False,
        "vat_rate": None,
        "vat_amount": "10.00",
    }
    assert expense(api, business, **given)["gross_amount"] == "60.00"
    assert call(api, business, "POST", "expenses", EXPENSE | {"vat_rate": None}).status_code == 422
    assert call(api, business, "POST", "expenses", EXPENSE | {"kind": "refund"}).status_code == 422
    assert (
        call(api, business, "POST", "expenses", EXPENSE | {"spent_on": "2026-02-30"}).status_code
        == 422
    )


def test_the_supplier_and_category_must_be_your_own(api, db, business):
    supplier_id = make(db, business, Supplier(name="Flour Co"))
    category = list_item(db, business, "cost_category", "Stock")
    ok = expense(api, business, supplier_id=supplier_id, cost_category_id=category)
    assert ok["supplier_id"] == supplier_id and ok["cost_category_id"] == category
    theirs = make(db, business, Supplier(name="Theirs"), org=1)
    assert (
        call(api, business, "POST", "expenses", EXPENSE | {"supplier_id": theirs}).status_code
        == 422
    )
    wrong = list_item(db, business, "sales_channel", "Shop")
    assert (
        call(api, business, "POST", "expenses", EXPENSE | {"cost_category_id": wrong}).status_code
        == 422
    )


def test_expense_references_are_unique(api, business):
    expense(api, business, reference="F-1")
    res = call(api, business, "POST", "expenses", EXPENSE | {"reference": "F-1"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "reference_taken"


def test_listing_filtering_and_reading_expenses(api, db, business):
    category = list_item(db, business, "cost_category", "Stock")
    expense(
        api,
        business,
        spent_on="2026-09-01",
        reference="E1",
        description="Flour",
        cost_category_id=category,
    )
    expense(api, business, spent_on="2026-09-10", reference="E2", description="Gas", kind="credit")

    def refs(**params):
        items = call(api, business, "GET", "expenses", **params).json()["items"]
        return sorted(i["reference"] for i in items)

    page = call(api, business, "GET", "expenses").json()
    assert page["total"] == 2 and [i["reference"] for i in page["items"]] == ["E2", "E1"]
    assert refs(date_from="2026-09-05") == ["E2"] and refs(kind="credit") == ["E2"]
    assert refs(cost_category_id=category) == ["E1"] and refs(q="flour") == ["E1"]
    assert refs(q="E2") == ["E2"]
    one = page["items"][0]
    assert call(api, business, "GET", f"expenses/{one['id']}").json() == one
    assert call(api, business, "GET", f"expenses/{uuid.uuid4()}").status_code == 404


def test_correcting_and_deleting_an_expense(api, db, business):
    made = expense(api, business)
    body = patch(
        api, business, "expenses", made["id"], {"description": "Rent", "supplier_id": None}
    )
    assert body.json()["description"] == "Rent" and body.json()["gross_amount"] == "60.00"
    money = {"amount": "100.00", "amount_includes_vat": False, "vat_rate": "20"}
    assert patch(api, business, "expenses", made["id"], money).json()["gross_amount"] == "120.00"
    assert patch(api, business, "expenses", made["id"], {"amount": "5"}).status_code == 422
    res = patch(api, business, "expenses", made["id"], {"kind": "credit"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "kind_needs_amount"
    assert patch(api, business, "expenses", made["id"], {"spent_on": None}).status_code == 422
    assert call(api, business, "DELETE", f"expenses/{made['id']}").status_code == 204
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Expense)) == 0


# --- who may do this -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["sales", "expenses"])
def test_viewers_cannot_use_any_of_it(api, business, path):
    body = SALE if path == "sales" else EXPENSE
    nothing = str(uuid.uuid4())
    assert call(api, business, "POST", path, body, who="viewer").status_code == 403
    assert call(api, business, "GET", path, who="viewer").status_code == 403
    assert call(api, business, "GET", f"{path}/{nothing}", who="viewer").status_code == 403
    assert call(api, business, "PATCH", f"{path}/{nothing}", {}, who="viewer").status_code == 403
    assert call(api, business, "DELETE", f"{path}/{nothing}", who="viewer").status_code == 403


@pytest.mark.parametrize("path", ["sales", "expenses"])
def test_logging_in_is_required(api, business, path):
    assert api.get(f"{ORGS}/{business[0]}/{path}").status_code == 401
    assert api.post(f"{ORGS}/{business[0]}/{path}", json={}).status_code == 401


def test_another_business_cannot_create_in_this_one(api, business):
    assert call(api, business, "POST", "sales", SALE, who="other", org=0).status_code == 404
