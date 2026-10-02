"""Typing customers, suppliers, products and stock movements in by hand."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.uk import today_uk
from app.db.tenant import tenant_scope
from app.models.business import BusinessListItem
from app.models.data import Customer, StockMovement
from app.models.identity import AuditLog, OrganizationUser, Role, User

ORGS = "/api/v1/organizations"
D = Decimal


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


def create(api, business, path, body, **kw):
    res = call(api, business, "POST", path, body, **kw)
    assert res.status_code == 201, res.text
    return res.json()


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def make(db, business, obj, org=0):
    with scoped(db, business, org):
        db.add(obj)
        db.flush()
        return str(obj.id)


def list_item(db, business, kind, name, *, active=True, org=0):
    return make(db, business, BusinessListItem(kind=kind, name=name, is_active=active), org)


def names(page):
    return [i["name"] for i in page["items"]]


# --- customers -----------------------------------------------------------------------------------


def test_a_customer_with_a_name_and_an_email(api, db, business):
    body = create(
        api,
        business,
        "customers",
        {"name": "  Jo   Bloggs ", "email": "JO@Example.com", "postcode": "ls1 4ap"},
    )
    assert body["name"] == "Jo Bloggs"  # spaces tidied
    assert (body["email"], body["postcode"]) == ("jo@example.com", "LS1 4AP")
    assert (body["source"], body["import_id"], body["is_active"]) == ("manual", None, True)


def test_a_customer_needs_a_name_or_an_email(api, business):
    assert create(api, business, "customers", {"name": "Jo"})["email"] is None
    assert create(api, business, "customers", {"email": "a@b.co.uk"})["name"] is None
    for body in ({}, {"postcode": "LS1 4AP"}, {"name": "", "email": ""}):
        assert call(api, business, "POST", "customers", body).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"email": "nope"},
        {"email": "a@@b.co.uk"},
        {"postcode": "90210"},
        {"postcode": "LS1"},
        {"name": "x" * 201},
        {"colour": "red"},
        {"is_active": "maybe"},
    ],
)
def test_bad_customers_are_refused(api, business, bad):
    res = call(api, business, "POST", "customers", {"name": "Jo"} | bad)
    assert res.status_code == 422, res.text


def test_a_customer_type_must_be_one_of_yours(api, db, business):
    kind = list_item(db, business, "customer_type", "Trade")
    assert (
        create(api, business, "customers", {"name": "Jo", "customer_type_id": kind})[
            "customer_type_id"
        ]
        == kind
    )

    def refused(item):
        res = call(api, business, "POST", "customers", {"name": "Jo", "customer_type_id": item})
        return res.status_code == 422

    assert refused(list_item(db, business, "sales_channel", "Shop"))  # the wrong list
    assert refused(list_item(db, business, "customer_type", "Old", active=False))
    assert refused(list_item(db, business, "customer_type", "Theirs", org=1))
    assert refused(str(uuid.uuid4()))


def test_listing_and_searching_customers(api, business):
    create(api, business, "customers", {"name": "Zoe", "email": "zoe@example.com"})
    create(api, business, "customers", {"name": "adam"})
    create(api, business, "customers", {"email": "bea@example.com"})
    create(api, business, "customers", {"name": "Inactive Ivy", "is_active": False})
    page = call(api, business, "GET", "customers").json()
    assert page["total"] == 4
    assert [i["name"] or i["email"] for i in page["items"]] == [
        "adam",
        "bea@example.com",
        "Inactive Ivy",
        "Zoe",
    ]  # alphabetical, ignoring case; a customer with no name sorts by email
    found = call(api, business, "GET", "customers", q="EXAMPLE").json()
    assert found["total"] == 2
    assert call(api, business, "GET", "customers", q="ada").json()["total"] == 1
    assert call(api, business, "GET", "customers", active="true").json()["total"] == 3
    assert call(api, business, "GET", "customers", active="false").json()["total"] == 1
    assert (
        call(api, business, "GET", "customers", limit=2, offset=3).json()["items"][0]["name"]
        == "Zoe"
    )


def test_search_treats_wildcards_literally(api, business):
    create(api, business, "customers", {"name": "100% Fruit"})
    create(api, business, "customers", {"name": "Plain"})
    assert call(api, business, "GET", "customers", q="%").json()["total"] == 1
    assert call(api, business, "GET", "customers", q="_").json()["total"] == 0


def test_get_change_and_archive_a_customer(api, business):
    made = create(api, business, "customers", {"name": "Jo", "email": "jo@example.com"})
    assert call(api, business, "GET", f"customers/{made['id']}").json() == made
    res = call(
        api, business, "PATCH", f"customers/{made['id']}", {"name": "Joanna", "postcode": "M1 1AE"}
    )
    assert (
        res.status_code == 200
        and res.json()["name"] == "Joanna"
        and res.json()["postcode"] == "M1 1AE"
    )
    gone = call(api, business, "PATCH", f"customers/{made['id']}", {"is_active": False}).json()
    assert gone["is_active"] is False


def test_a_customer_must_keep_a_name_or_an_email(api, business):
    made = create(api, business, "customers", {"name": "Jo", "email": "jo@example.com"})
    assert (
        call(api, business, "PATCH", f"customers/{made['id']}", {"name": None}).json()["name"]
        is None
    )
    res = call(api, business, "PATCH", f"customers/{made['id']}", {"email": None})
    assert res.status_code == 422 and res.json()["error"]["code"] == "someone_needed"
    assert call(api, business, "GET", f"customers/{made['id']}").json()["email"] == "jo@example.com"


def test_a_customer_with_sales_cannot_be_deleted_only_archived(api, db, business):
    made = create(api, business, "customers", {"name": "Jo"})
    create(
        api,
        business,
        "sales",
        {
            "sold_on": "2026-09-28",
            "amount": "12.00",
            "amount_includes_vat": True,
            "vat_rate": "20",
            "customer_id": made["id"],
        },
    )
    res = call(api, business, "DELETE", f"customers/{made['id']}")
    error = res.json()["error"]
    assert res.status_code == 409 and error["code"] == "in_use" and "Archive" in error["message"]
    assert call(api, business, "GET", f"customers/{made['id']}").status_code == 200
    assert (
        call(api, business, "PATCH", f"customers/{made['id']}", {"is_active": False}).status_code
        == 200
    )


def test_an_unused_customer_can_be_deleted(api, db, business):
    made = create(api, business, "customers", {"name": "Jo"})
    assert call(api, business, "DELETE", f"customers/{made['id']}").status_code == 204
    assert call(api, business, "GET", f"customers/{made['id']}").status_code == 404
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "record.deleted")).one()
    assert entry.target_type == "customers"


def test_customers_belong_to_one_business(api, business):
    made = create(api, business, "customers", {"name": "Mine"})
    create(api, business, "customers", {"name": "Theirs"}, who="other", org=1)
    assert names(call(api, business, "GET", "customers").json()) == ["Mine"]
    for org in (0, 1):
        for method, body in (("GET", None), ("PATCH", {"name": "x"}), ("DELETE", None)):
            res = call(api, business, method, f"customers/{made['id']}", body, who="other", org=org)
            assert res.status_code == 404


# --- suppliers ---------------------------------------------------------------------------------


def test_suppliers_end_to_end(api, db, business):
    made = create(api, business, "suppliers", {"name": "Flour Co"})
    assert (made["source"], made["is_active"]) == ("manual", True)
    create(api, business, "suppliers", {"name": "apple orchard"})
    assert names(call(api, business, "GET", "suppliers").json()) == ["apple orchard", "Flour Co"]
    assert names(call(api, business, "GET", "suppliers", q="FLOUR").json()) == ["Flour Co"]
    changed = call(api, business, "PATCH", f"suppliers/{made['id']}", {"name": "Flour Ltd"}).json()
    assert changed["name"] == "Flour Ltd"
    assert (
        call(api, business, "PATCH", f"suppliers/{made['id']}", {"name": None}).status_code == 422
    )
    for bad in ({}, {"name": ""}, {"name": "x" * 201}, {"name": "A", "colour": "red"}):
        assert call(api, business, "POST", "suppliers", bad).status_code == 422
    assert call(api, business, "GET", f"suppliers/{uuid.uuid4()}").status_code == 404


def test_a_supplier_with_expenses_cannot_be_deleted(api, business):
    made = create(api, business, "suppliers", {"name": "Flour Co"})
    expense = {
        "spent_on": "2026-09-03",
        "amount": "60.00",
        "amount_includes_vat": True,
        "vat_rate": "20",
        "supplier_id": made["id"],
    }
    create(api, business, "expenses", expense)
    res = call(api, business, "DELETE", f"suppliers/{made['id']}")
    assert res.status_code == 409 and res.json()["error"]["code"] == "in_use"
    other = create(api, business, "suppliers", {"name": "Unused"})
    assert call(api, business, "DELETE", f"suppliers/{other['id']}").status_code == 204


# --- products ----------------------------------------------------------------------------------


def test_a_product(api, db, business):
    offering = list_item(db, business, "offering", "Bread")
    body = create(
        api,
        business,
        "products",
        {
            "name": "Sourdough",
            "sku": "SD-800",
            "offering_id": offering,
            "unit_price_ex_vat": "4.5",
            "unit_cost": "1.2345",
            "vat_rate": "0",
        },
    )
    assert (body["name"], body["sku"], body["offering_id"]) == ("Sourdough", "SD-800", offering)
    assert D(body["unit_price_ex_vat"]) == D("4.5") and D(body["unit_cost"]) == D("1.2345")
    assert D(body["vat_rate"]) == 0 and body["source"] == "manual"
    assert create(api, business, "products", {"name": "Bare"})["unit_price_ex_vat"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"unit_price_ex_vat": "-1"},
        {"unit_cost": "-0.01"},
        {"unit_price_ex_vat": "1.23456"},
        {"vat_rate": "17.5"},
        {"sku": ""},
        {"colour": "red"},
    ],
)
def test_bad_products_are_refused(api, business, bad):
    assert call(api, business, "POST", "products", {"name": "Loaf"} | bad).status_code == 422


def test_a_product_code_can_be_used_once(api, business):
    create(api, business, "products", {"name": "A", "sku": "X1"})
    res = call(api, business, "POST", "products", {"name": "B", "sku": "x1"})
    assert res.status_code == 201  # codes are matched exactly, so "x1" is a different code
    res = call(api, business, "POST", "products", {"name": "B", "sku": "X1"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "sku_taken"
    create(api, business, "products", {"name": "C"})  # no code is never a clash
    create(api, business, "products", {"name": "D"})
    other = call(api, business, "POST", "products", {"name": "A", "sku": "X1"}, who="other", org=1)
    assert other.status_code == 201


def test_products_can_be_listed_searched_and_changed(api, business):
    loaf = create(api, business, "products", {"name": "Loaf", "sku": "L-1"})
    create(api, business, "products", {"name": "bun", "sku": "B-1", "is_active": False})
    assert names(call(api, business, "GET", "products").json()) == ["bun", "Loaf"]
    assert names(call(api, business, "GET", "products", q="l-1").json()) == ["Loaf"]
    assert names(call(api, business, "GET", "products", active="false").json()) == ["bun"]
    body = call(
        api,
        business,
        "PATCH",
        f"products/{loaf['id']}",
        {"unit_price_ex_vat": "5", "vat_rate": "20", "sku": None},
    ).json()
    assert D(body["unit_price_ex_vat"]) == 5 and D(body["vat_rate"]) == 20 and body["sku"] is None
    clash = call(api, business, "PATCH", f"products/{loaf['id']}", {"sku": "B-1"})
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "sku_taken"
    assert call(api, business, "PATCH", f"products/{loaf['id']}", {"name": None}).status_code == 422


def test_a_product_in_use_is_archived_not_deleted(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    sale = {
        "sold_on": "2026-09-28",
        "amount": "12.00",
        "amount_includes_vat": True,
        "vat_rate": "20",
        "lines": [{"product_id": loaf["id"], "quantity": "1", "net_amount": "10.00"}],
    }
    create(api, business, "sales", sale)
    res = call(api, business, "DELETE", f"products/{loaf['id']}")
    assert res.status_code == 409 and res.json()["error"]["code"] == "in_use"
    spare = create(api, business, "products", {"name": "Spare"})
    assert call(api, business, "DELETE", f"products/{spare['id']}").status_code == 204


# --- stock movements ------------------------------------------------------------------------------


def move(api, business, product_id, **fields):
    body = {
        "product_id": product_id,
        "moved_on": "2026-09-01",
        "kind": "delivery",
        "quantity": "10",
    }
    return call(api, business, "POST", "stock-movements", body | fields)


@pytest.mark.parametrize(
    ("kind", "quantity", "ok"),
    [
        ("opening", "40", True),
        ("opening", "0", True),
        ("opening", "-1", False),
        ("delivery", "12", True),
        ("delivery", "0", False),
        ("delivery", "-12", False),
        ("return", "1", True),
        ("return", "-1", False),
        ("sale", "-2", True),
        ("sale", "2", False),
        ("write_off", "-3", True),
        ("write_off", "3", False),
        ("adjustment", "-1", True),
        ("adjustment", "1", True),
        ("adjustment", "0", False),
    ],
)
def test_the_direction_of_a_movement_must_match_its_kind(api, business, kind, quantity, ok):
    loaf = create(api, business, "products", {"name": "Loaf"})
    res = move(api, business, loaf["id"], kind=kind, quantity=quantity)
    assert (res.status_code == 201) is ok, res.text
    if not ok:
        assert res.status_code == 422 and kind in res.text


def test_a_stock_movement(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    body = move(api, business, loaf["id"], unit_cost="2.5", notes="From the mill").json()
    assert (body["kind"], D(body["quantity"]), D(body["unit_cost"])) == ("delivery", 10, D("2.5"))
    assert (body["source"], body["import_id"], body["notes"]) == ("manual", None, "From the mill")


def test_stock_movements_need_one_of_your_products(api, db, business):
    assert move(api, business, str(uuid.uuid4())).status_code == 422
    theirs = create(api, business, "products", {"name": "Theirs"}, who="other", org=1)
    res = move(api, business, theirs["id"])
    assert res.status_code == 422 and res.json()["error"]["details"]["field"] == "product_id"
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(StockMovement)) == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"moved_on": "2026-02-30"},
        {"moved_on": "2099-01-01"},
        {"kind": "teleport"},
        {"unit_cost": "-1"},
        {"quantity": "abc"},
        {"quantity": "1.00001"},
        {"colour": "red"},
    ],
)
def test_bad_stock_movements_are_refused(api, business, bad):
    loaf = create(api, business, "products", {"name": "Loaf"})
    assert move(api, business, loaf["id"], **bad).status_code == 422


def test_stock_on_hand_is_the_sum_of_movements(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    for day, kind, qty in [
        ("2026-09-01", "opening", "40"),
        ("2026-09-02", "delivery", "24"),
        ("2026-09-03", "sale", "-30"),
        ("2026-09-04", "write_off", "-4"),
    ]:
        assert (
            move(api, business, loaf["id"], moved_on=day, kind=kind, quantity=qty).status_code
            == 201
        )

    def level(**params):
        res = call(api, business, "GET", f"products/{loaf['id']}/stock", **params)
        assert res.status_code == 200, res.text
        return D(res.json()["quantity"]), res.json()["as_of"]

    assert level(as_of="2026-09-04") == (D("30"), "2026-09-04")
    assert level(as_of="2026-09-03") == (D("34"), "2026-09-03")
    assert level(as_of="2026-09-02")[0] == D("64") and level(as_of="2026-08-31")[0] == 0
    assert level() == (D("30"), today_uk().isoformat())  # default: today in the UK
    assert call(api, business, "GET", f"products/{uuid.uuid4()}/stock").status_code == 404
    assert (
        call(api, business, "GET", f"products/{loaf['id']}/stock", as_of="soon").status_code == 422
    )


def test_listing_and_filtering_movements(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    bun = create(api, business, "products", {"name": "Bun"})
    move(api, business, loaf["id"], moved_on="2026-09-01", kind="opening", quantity="5")
    move(api, business, loaf["id"], moved_on="2026-09-03", kind="sale", quantity="-1")
    move(api, business, bun["id"], moved_on="2026-09-02")
    page = call(api, business, "GET", "stock-movements").json()
    assert page["total"] == 3 and [m["moved_on"] for m in page["items"]] == [
        "2026-09-03",
        "2026-09-02",
        "2026-09-01",
    ]
    assert call(api, business, "GET", "stock-movements", product_id=loaf["id"]).json()["total"] == 2
    assert call(api, business, "GET", "stock-movements", kind="sale").json()["total"] == 1
    assert (
        call(
            api, business, "GET", "stock-movements", date_from="2026-09-02", date_to="2026-09-02"
        ).json()["total"]
        == 1
    )
    assert call(api, business, "GET", "stock-movements", kind="teleport").status_code == 422
    assert (
        call(api, business, "GET", "stock-movements", limit=1, offset=1).json()["items"][0][
            "moved_on"
        ]
        == "2026-09-02"
    )


def test_delete_a_stock_movement(api, db, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    made = move(api, business, loaf["id"]).json()
    assert call(api, business, "DELETE", f"stock-movements/{made['id']}").status_code == 204
    assert call(api, business, "DELETE", f"stock-movements/{made['id']}").status_code == 404
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(StockMovement)) == 0
    # and a product can then be deleted
    assert call(api, business, "DELETE", f"products/{loaf['id']}").status_code == 204


def test_a_product_with_stock_movements_cannot_be_deleted(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    move(api, business, loaf["id"])
    res = call(api, business, "DELETE", f"products/{loaf['id']}")
    assert res.status_code == 409 and res.json()["error"]["code"] == "in_use"


def test_another_business_cannot_touch_these_movements(api, business):
    loaf = create(api, business, "products", {"name": "Loaf"})
    made = move(api, business, loaf["id"]).json()
    for org in (0, 1):
        res = call(api, business, "DELETE", f"stock-movements/{made['id']}", who="other", org=org)
        assert res.status_code == 404
    assert call(api, business, "GET", "stock-movements", who="other", org=1).json()["total"] == 0


# --- audit and permissions ------------------------------------------------------------------------


def test_creating_changing_and_deleting_are_audited(api, db, business):
    made = create(api, business, "suppliers", {"name": "Flour Co"})
    call(api, business, "PATCH", f"suppliers/{made['id']}", {"name": "Flour Ltd"})
    call(api, business, "DELETE", f"suppliers/{made['id']}")
    actions = db.scalars(
        select(AuditLog.action)
        .where(AuditLog.target_type == "suppliers")
        .order_by(AuditLog.created_at)
    ).all()
    assert actions == ["record.created", "record.updated", "record.deleted"]
    details = db.scalars(select(AuditLog.details).where(AuditLog.action == "record.created")).all()
    assert all("Flour" not in str(d) for d in details)  # no names or emails in the audit trail


@pytest.mark.parametrize("path", ["customers", "suppliers", "products", "stock-movements"])
def test_viewers_cannot_use_any_of_it(api, business, path):
    nothing = str(uuid.uuid4())
    assert call(api, business, "POST", path, {}, who="viewer").status_code == 403
    assert call(api, business, "GET", path, who="viewer").status_code == 403
    assert call(api, business, "DELETE", f"{path}/{nothing}", who="viewer").status_code == 403
    if path != "stock-movements":
        assert call(api, business, "GET", f"{path}/{nothing}", who="viewer").status_code == 403
        assert (
            call(api, business, "PATCH", f"{path}/{nothing}", {}, who="viewer").status_code == 403
        )
    if path == "products":
        assert (
            call(api, business, "GET", f"products/{nothing}/stock", who="viewer").status_code == 403
        )


@pytest.mark.parametrize("path", ["customers", "suppliers", "products", "stock-movements"])
def test_logging_in_is_required(api, business, path):
    assert api.get(f"{ORGS}/{business[0]}/{path}").status_code == 401
    assert api.post(f"{ORGS}/{business[0]}/{path}", json={}).status_code == 401


def test_nothing_imported_is_touched_by_the_manual_lists(api, db, business):
    """Records that came from a file appear in the lists, marked with their source."""
    make(db, business, Customer(name="From a file", source="csv"))
    page = call(api, business, "GET", "customers").json()
    assert page["items"][0]["source"] == "csv"
