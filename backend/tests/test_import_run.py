"""Importing checked rows into the business's data, undoing it, and the import history."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.tenant import tenant_scope
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
from app.models.identity import AuditLog
from app.models.imports import DataImport, DataImportRow, DataSource
from app.services import import_records, import_runner
from app.services.import_rows import RowOutcome

ORGS = "/api/v1/organizations"
D = Decimal
INC = {"vat_inclusive": True}
SALES_MAP = {"sold_on": "Date", "amount": "Total", "vat_amount": "VAT", "reference": "Order"}
ROWS = ("28/09/2026,1001,12.00,2.00", "29/09/2026,1002,24.00,4.00", "30/09/2026,1003,6.00,1.00")


def base(business, import_id, org=0):
    return f"{ORGS}/{business[org]}/imports/{import_id}"


def csv(*rows, header="Date,Order,Total,VAT"):
    return ("\n".join([header, *rows]) + "\n").encode()


def checked(
    api, business, content, mapping=SALES_MAP, options=INC, dataset="sales", org=0, who="owner"
):
    """Upload, map and check a file; returns the import id, ready to import."""
    headers = business[2][who]
    res = api.post(
        f"{ORGS}/{business[org]}/imports",
        files={"file": ("file.csv", content)},
        data={"dataset": dataset},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    import_id = res.json()["id"]
    res = api.put(
        base(business, import_id, org) + "/mapping",
        json={"mapping": mapping, "options": options},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    res = api.post(base(business, import_id, org) + "/validate", headers=headers)
    assert res.status_code == 200, res.text
    return import_id


def run(api, business, import_id, who="owner", org=0):
    return api.post(base(business, import_id, org) + "/import", headers=business[2][who])


def undo(api, business, import_id, who="owner", org=0):
    return api.post(base(business, import_id, org) + "/undo", headers=business[2][who])


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


# --- sales ----------------------------------------


def test_importing_sales_creates_them(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    res = run(api, business, import_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] == {"sales": 3}
    assert (body["skipped_duplicates"], body["skipped_invalid"]) == (0, 0)
    assert body["data_import"]["status"] == "imported"
    assert body["data_import"]["imported_count"] == 3 and body["data_import"]["imported_at"]
    with scoped(db, business):
        sales = db.scalars(select(Sale).order_by(Sale.sold_on)).all()
        assert [s.sold_on for s in sales] == [
            date(2026, 9, 28),
            date(2026, 9, 29),
            date(2026, 9, 30),
        ]
        first = sales[0]
        assert (first.net_amount, first.vat_amount, first.gross_amount) == (
            D("10"),
            D("2"),
            D("12"),
        )
        assert (first.kind, first.currency, first.source, first.source_ref) == (
            "sale",
            "GBP",
            "csv",
            "1001",
        )
        assert str(first.import_id) == import_id
        assert count(db, SaleLine) == 0  # no product, quantity or cost on these rows


def test_the_imported_total_matches_what_checking_promised(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    promised = api.get(
        base(business, import_id) + "/validation", headers=business[2]["owner"]
    ).json()
    run(api, business, import_id)
    with scoped(db, business):
        net, vat, gross = db.execute(
            select(
                func.sum(Sale.net_amount), func.sum(Sale.vat_amount), func.sum(Sale.gross_amount)
            )
        ).one()
    assert {"net": f"{net:.2f}", "vat": f"{vat:.2f}", "gross": f"{gross:.2f}"} == promised["totals"]


def test_each_row_points_at_the_record_it_became(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    run(api, business, import_id)
    with scoped(db, business):
        rows = db.scalars(select(DataImportRow).order_by(DataImportRow.row_number)).all()
        sale_ids = {s.source_ref: s.id for s in db.scalars(select(Sale))}
        assert [r.status for r in rows] == ["imported"] * 3
        assert [r.target_table for r in rows] == ["sales"] * 3
        assert [r.target_id for r in rows] == [sale_ids["1001"], sale_ids["1002"], sale_ids["1003"]]


def test_importing_is_audited_and_the_source_remembers_it(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    api.put(
        base(business, import_id) + "/mapping",
        json={"mapping": SALES_MAP, "options": INC, "save_as": "Till"},
        headers=business[2]["owner"],
    )
    api.post(base(business, import_id) + "/validate", headers=business[2]["owner"])
    run(api, business, import_id)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "import.completed")).one()
    assert entry.target_id == import_id
    assert entry.details["imported"] == 3 and entry.details["created"] == {"sales": 3}
    with scoped(db, business):
        source = db.scalars(select(DataSource)).one()
        assert source.last_imported_at is not None


def test_the_sale_line_describes_what_was_sold(api, db, business):
    mapping = SALES_MAP | {"product": "Item", "quantity": "Qty", "cost": "Cost", "sku": "SKU"}
    content = csv(
        "28/09/2026,1001,12.00,2.00,Sourdough,2,3.10,SD1",
        "29/09/2026,1002,6.00,1.00,Sourdough,1,1.55,SD1",
        header="Date,Order,Total,VAT,Item,Qty,Cost,SKU",
    )
    res = run(api, business, checked(api, business, content, mapping))
    assert res.json()["created"] == {"products": 1, "sales": 2, "sale_lines": 2}
    with scoped(db, business):
        [product] = db.scalars(select(Product)).all()
        assert (product.name, product.sku, product.source) == ("Sourdough", "SD1", "csv")
        lines = db.scalars(select(SaleLine).order_by(SaleLine.quantity.desc())).all()
        assert [line.quantity for line in lines] == [D("2"), D("1")]
        assert {line.product_id for line in lines} == {product.id}
        assert lines[0].unit_price_ex_vat == D("5.0000") and lines[0].cost_amount == D("3.10")
        assert lines[0].net_amount == D("10") and lines[0].vat_amount == D("2")
        assert lines[0].description == "Sourdough"


def test_a_row_with_only_a_quantity_gets_a_line_without_a_product(api, db, business):
    mapping = SALES_MAP | {"quantity": "Qty"}
    content = csv("28/09/2026,1001,12.00,2.00,3", header="Date,Order,Total,VAT,Qty")
    run(api, business, checked(api, business, content, mapping))
    with scoped(db, business):
        [line] = db.scalars(select(SaleLine)).all()
        assert line.product_id is None and line.quantity == D("3")
        assert line.unit_price_ex_vat == D("3.3333")


def test_products_that_already_exist_are_reused_not_duplicated(api, db, business):
    with scoped(db, business):
        db.add(Product(name="Sourdough loaf", sku="SD1"))
        db.add(Product(name="Bun"))
        db.flush()
    mapping = SALES_MAP | {"product": "Item", "sku": "SKU"}
    content = csv(
        "28/09/2026,1,12.00,2.00,whatever it is called here,SD1",
        "28/09/2026,2,6.00,1.00,BUN,",
        header="Date,Order,Total,VAT,Item,SKU",
    )
    res = run(api, business, checked(api, business, content, mapping))
    assert "products" not in res.json()["created"]
    with scoped(db, business):
        assert count(db, Product) == 2
        assert {x.product_id for x in db.scalars(select(SaleLine))} == set(
            db.scalars(select(Product.id))
        )


def test_customers_are_found_by_email_or_name_and_created_once(api, db, business):
    with scoped(db, business):
        db.add(Customer(name="Old Friend", email="friend@example.com"))
        db.add(Customer(name="Sam Smith"))
        db.flush()
    mapping = SALES_MAP | {
        "customer_name": "Name",
        "customer_email": "Email",
        "customer_postcode": "Postcode",
    }
    content = csv(
        "28/09/2026,1,12.00,2.00,Whoever,FRIEND@example.com,",  # existing, by email
        "28/09/2026,2,12.00,2.00,sam smith,,",  # existing, by name
        "28/09/2026,3,12.00,2.00,Jo Bloggs,jo@example.com,ls1 4ap",  # new
        "28/09/2026,4,12.00,2.00,Jo B,jo@example.com,",  # same email as the new one
        "28/09/2026,5,12.00,2.00,Pat,,",  # new, name only
        "28/09/2026,6,12.00,2.00,PAT,,",  # same name again
        "28/09/2026,7,12.00,2.00,,,",  # no customer at all
        header="Date,Order,Total,VAT,Name,Email,Postcode",
    )
    res = run(api, business, checked(api, business, content, mapping))
    assert res.json()["created"] == {"customers": 2, "sales": 7}
    with scoped(db, business):
        by_ref = {s.source_ref: s.customer_id for s in db.scalars(select(Sale))}
        customers = {c.id: c for c in db.scalars(select(Customer))}
        assert len(customers) == 4
        assert customers[by_ref["1"]].email == "friend@example.com"
        assert customers[by_ref["2"]].name == "Sam Smith"
        assert by_ref["3"] == by_ref["4"] and by_ref["5"] == by_ref["6"] and by_ref["7"] is None
        jo = customers[by_ref["3"]]
        assert (jo.name, jo.email, jo.postcode, jo.source) == (
            "Jo Bloggs",
            "jo@example.com",
            "LS1 4AP",
            "csv",
        )
        assert customers[by_ref["1"]].import_id is None  # existing ones are left alone


def test_sales_channels_come_from_the_businesss_own_list(api, db, business):
    with scoped(db, business):
        db.add(BusinessListItem(kind="sales_channel", name="Website"))
        db.flush()
    mapping = SALES_MAP | {"channel": "Channel"}
    content = csv(
        "28/09/2026,1,12.00,2.00,website",
        "28/09/2026,2,12.00,2.00,Market stall",
        "28/09/2026,3,12.00,2.00,market STALL",
        "28/09/2026,4,12.00,2.00,",
        header="Date,Order,Total,VAT,Channel",
    )
    res = run(api, business, checked(api, business, content, mapping))
    assert res.json()["created"] == {"business_list_items": 1, "sales": 4}
    with scoped(db, business):
        items = {i.id: i.name for i in db.scalars(select(BusinessListItem))}
        by_ref = {s.source_ref: s.sales_channel_id for s in db.scalars(select(Sale))}
        assert sorted(items.values()) == ["Market stall", "Website"]
        assert items[by_ref["1"]] == "Website" and by_ref["4"] is None
        assert by_ref["2"] == by_ref["3"]


def test_refunds_come_in_negative(api, db, business):
    mapping = SALES_MAP | {"quantity": "Qty", "product": "Item"}
    content = csv(
        "30/09/2026,R1,-12.00,-2.00,2,Loaf",
        "30/09/2026,R2,(6.00),1.00,3,Loaf",
        header="Date,Order,Total,VAT,Qty,Item",
    )
    run(api, business, checked(api, business, content, mapping))
    with scoped(db, business):
        sales = {s.source_ref: s for s in db.scalars(select(Sale))}
        assert sales["R1"].kind == "refund"
        assert (sales["R1"].net_amount, sales["R1"].gross_amount) == (D("-10"), D("-12"))
        lines = {x.sale_id: x for x in db.scalars(select(SaleLine))}
        assert lines[sales["R1"].id].quantity == D("-2")
        assert lines[sales["R2"].id].quantity == D("-3")
        assert lines[sales["R1"].id].unit_price_ex_vat == D("5.0000")  # never negative


def test_discounts_are_kept(api, db, business):
    mapping = SALES_MAP | {"discount": "Discount"}
    content = csv("28/09/2026,1,12.00,2.00,-1.50", header="Date,Order,Total,VAT,Discount")
    run(api, business, checked(api, business, content, mapping))
    with scoped(db, business):
        assert db.scalars(select(Sale.discount_amount)).one() == D("1.50")


def test_sales_without_a_reference_import_even_when_identical(api, db, business):
    mapping = {"sold_on": "Date", "amount": "Total", "vat_amount": "VAT"}
    content = csv(*["28/09/2026,1,12.00,2.00"] * 3)
    res = run(api, business, checked(api, business, content, mapping))
    assert res.json()["created"] == {"sales": 3}


# --- the other kinds of file ----------------------------------------


def test_importing_expenses(api, db, business):
    with scoped(db, business):
        db.add(Supplier(name="Flour Co"))
        db.flush()
    mapping = {
        "spent_on": "Date",
        "amount": "Net",
        "vat_amount": "VAT",
        "supplier": "Supplier",
        "category": "Category",
        "reference": "Invoice",
        "description": "What",
    }
    content = csv(
        "03/09/2026,50.00,10.00,flour co,Stock,F-1,Flour",
        "04/09/2026,100.00,20.00,Gas Ltd,Utilities,G-1,Gas",
        "05/09/2026,-20.00,-4.00,Gas Ltd,utilities,G-2,Refund",
        header="Date,Net,VAT,Supplier,Category,Invoice,What",
    )
    res = run(
        api,
        business,
        checked(api, business, content, mapping, {"vat_inclusive": False}, "expenses"),
    )
    assert res.json()["created"] == {"expenses": 3, "suppliers": 1, "business_list_items": 2}
    with scoped(db, business):
        expenses = {e.source_ref: e for e in db.scalars(select(Expense))}
        assert (expenses["F-1"].net_amount, expenses["F-1"].gross_amount) == (D("50"), D("60"))
        assert expenses["G-2"].kind == "credit" and expenses["G-2"].gross_amount == D("-24")
        suppliers = {s.name: s for s in db.scalars(select(Supplier))}
        assert expenses["F-1"].supplier_id == suppliers["Flour Co"].id
        assert expenses["G-1"].supplier_id == expenses["G-2"].supplier_id == suppliers["Gas Ltd"].id
        assert (
            suppliers["Gas Ltd"].import_id is not None and suppliers["Flour Co"].import_id is None
        )
        categories = {i.name for i in db.scalars(select(BusinessListItem))}
        assert categories == {"Stock", "Utilities"}
        assert expenses["G-1"].cost_category_id == expenses["G-2"].cost_category_id
        assert expenses["F-1"].description == "Flour"


def test_importing_customers(api, db, business):
    mapping = {
        "name": "Name",
        "email": "Email",
        "postcode": "Postcode",
        "customer_type": "Type",
        "reference": "ID",
    }
    content = csv(
        "Jo Bloggs,JO@x.co.uk,ls14ap,Trade,C1",
        "Sam,,,trade,C2",
        header="Name,Email,Postcode,Type,ID",
    )
    res = run(api, business, checked(api, business, content, mapping, {}, "customers"))
    assert res.json()["created"] == {"customers": 2, "business_list_items": 1}
    with scoped(db, business):
        customers = {c.source_ref: c for c in db.scalars(select(Customer))}
        assert customers["C1"].email == "jo@x.co.uk" and customers["C1"].postcode == "LS1 4AP"
        assert customers["C1"].customer_type_id == customers["C2"].customer_type_id is not None


def test_importing_suppliers(api, db, business):
    res = run(
        api,
        business,
        checked(
            api,
            business,
            b"Name,ID\nFlour Co,S1\nGas Ltd,S2\n",
            {"name": "Name", "reference": "ID"},
            {},
            "suppliers",
        ),
    )
    assert res.json()["created"] == {"suppliers": 2}
    with scoped(db, business):
        assert {s.source_ref for s in db.scalars(select(Supplier))} == {"S1", "S2"}


def test_importing_products(api, db, business):
    mapping = {
        "name": "Name",
        "sku": "SKU",
        "unit_price": "Price",
        "unit_cost": "Cost",
        "vat_rate": "VAT",
        "category": "Range",
    }
    content = csv(
        "Sourdough,SD1,£6.00,2.10,20,Bread",
        "Bun,B1,£1.05,0.30,5,bread",
        header="Name,SKU,Price,Cost,VAT,Range",
    )
    res = run(api, business, checked(api, business, content, mapping, INC, "products"))
    assert res.json()["created"] == {"products": 2, "business_list_items": 1}
    with scoped(db, business):
        products = {p.sku: p for p in db.scalars(select(Product))}
        assert products["SD1"].unit_price_ex_vat == D("5.0000") and products["SD1"].vat_rate == D(
            "20"
        )
        assert products["B1"].unit_price_ex_vat == D("1.0000") and products["B1"].vat_rate == D("5")
        assert products["SD1"].unit_cost == D("2.10")
        assert products["SD1"].offering_id == products["B1"].offering_id is not None


def test_importing_stock_movements(api, db, business):
    with scoped(db, business):
        db.add(Product(name="Loaf", sku="SD1"))
        db.flush()
    mapping = {
        "moved_on": "Date",
        "sku": "SKU",
        "quantity": "Qty",
        "movement_kind": "Type",
        "unit_cost": "Cost",
    }
    content = csv(
        "01/09/2026,SD1,40,opening,2.10",
        "02/09/2026,SD1,-3,write off,",
        "03/09/2026,NEW1,12,,1.00",
        header="Date,SKU,Qty,Type,Cost",
    )
    res = run(api, business, checked(api, business, content, mapping, {}, "stock_movements"))
    assert res.json()["created"] == {"products": 1, "stock_movements": 3}
    with scoped(db, business):
        moves = db.scalars(select(StockMovement).order_by(StockMovement.moved_on)).all()
        assert [(m.kind, m.quantity) for m in moves] == [
            ("opening", D("40")),
            ("write_off", D("-3")),
            ("delivery", D("12")),
        ]
        products = {p.sku: p.id for p in db.scalars(select(Product))}
        assert moves[0].product_id == moves[1].product_id == products["SD1"]
        assert moves[2].product_id == products["NEW1"] and moves[0].unit_cost == D("2.10")


# --- what gets skipped ----------------------------------------


def test_only_valid_rows_are_imported(api, db, business):
    content = csv("28/09/2026,1,12.00,2.00", "31/04/2026,2,12.00,2.00", "28/09/2026,1,12.00,2.00")
    import_id = checked(api, business, content)
    body = run(api, business, import_id).json()
    assert body["created"] == {"sales": 1}
    with scoped(db, business):
        statuses = [
            r.status for r in db.scalars(select(DataImportRow).order_by(DataImportRow.row_number))
        ]
    assert statuses == ["imported", "invalid", "duplicate"]
    imp = body["data_import"]
    assert (
        imp["imported_count"],
        imp["valid_count"],
        imp["invalid_count"],
        imp["duplicate_count"],
    ) == (1, 1, 1, 1)


def test_a_record_added_since_checking_is_skipped_not_doubled(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    with scoped(db, business):
        db.add(
            Sale(
                sold_on=date(2026, 9, 29),
                net_amount=D("20"),
                vat_amount=D("4"),
                gross_amount=D("24"),
                source="csv",
                source_ref="1002",
            )
        )
        db.flush()
    body = run(api, business, import_id).json()
    assert body["created"] == {"sales": 2} and body["skipped_duplicates"] == 1
    assert (
        body["data_import"]["duplicate_count"] == 1 and body["data_import"]["imported_count"] == 2
    )
    with scoped(db, business):
        skipped = db.scalars(select(DataImportRow).where(DataImportRow.status == "duplicate")).one()
        assert skipped.errors[0]["code"] == "already_imported"


def test_uploading_the_same_orders_again_after_importing_finds_every_row_a_repeat(
    api, db, business
):
    run(api, business, checked(api, business, csv(*ROWS)))
    again = checked(api, business, csv(*ROWS))
    body = api.get(base(business, again) + "/validation", headers=business[2]["owner"]).json()
    assert (body["valid"], body["duplicate"]) == (0, 3)
    res = run(api, business, again)
    assert res.status_code == 409 and res.json()["error"]["code"] == "nothing_to_import"
    with scoped(db, business):
        assert count(db, Sale) == 3


def test_a_big_file_is_built_in_batches(api, db, business, monkeypatch):
    monkeypatch.setattr(import_runner, "BATCH", 3)
    rows = [f"28/09/2026,{n},10.00,0" for n in range(1, 11)]
    mapping = SALES_MAP | {"product": "Item"}
    rows = [r + (",Loaf" if n % 2 else ",Bun") for n, r in enumerate(rows)]
    content = csv(*rows, header="Date,Order,Total,VAT,Item")
    res = run(api, business, checked(api, business, content, mapping))
    assert res.json()["created"] == {"products": 2, "sales": 10, "sale_lines": 10}
    with scoped(db, business):
        assert count(db, Sale) == 10 and count(db, Product) == 2


# --- when it can't be done ----------------------------------------


def test_it_must_be_checked_first(api, business):
    res = api.post(
        f"{ORGS}/{business[0]}/imports",
        files={"file": ("f.csv", csv(*ROWS))},
        data={"dataset": "sales"},
        headers=business[2]["owner"],
    )
    import_id = res.json()["id"]
    for _ in range(2):  # uploaded, then mapped but not checked
        out = run(api, business, import_id)
        assert out.status_code == 409 and out.json()["error"]["code"] == "not_validated"
        api.put(
            base(business, import_id) + "/mapping",
            json={"mapping": SALES_MAP, "options": INC},
            headers=business[2]["owner"],
        )


def test_it_cannot_be_done_twice(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    run(api, business, import_id)
    again = run(api, business, import_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_imported"
    with scoped(db, business):
        assert count(db, Sale) == 3


def test_nothing_valid_means_nothing_to_import(api, business):
    import_id = checked(api, business, csv("nope,1,12.00,2.00"))
    res = run(api, business, import_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "nothing_to_import"


def test_the_original_file_must_not_have_expired(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    with scoped(db, business):
        db.get(DataImport, uuid.UUID(import_id)).file_deleted_at = datetime.now(UTC)
        db.flush()
    res = run(api, business, import_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "file_deleted"


def test_a_failure_part_way_imports_nothing(api, db, business, monkeypatch):
    monkeypatch.setattr(import_runner, "BATCH", 2)
    real = import_records.insert_records
    calls = []

    def flaky(db_, tables, created):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the database went away")
        return real(db_, tables, created)

    monkeypatch.setattr(import_records, "insert_records", flaky)
    mapping = SALES_MAP | {"customer_name": "Name"}
    content = csv(
        *[f"28/09/2026,{n},12.00,2.00,Cust {n}" for n in range(1, 6)],
        header="Date,Order,Total,VAT,Name",
    )
    import_id = checked(api, business, content, mapping)
    assert run(api, business, import_id).status_code == 500
    assert len(calls) == 2  # the first batch had been written when the second failed
    with scoped(db, business):
        assert (count(db, Sale), count(db, Customer)) == (0, 0)
        imp = db.get(DataImport, uuid.UUID(import_id), populate_existing=True)
        assert imp.status == "validated" and imp.imported_count == 0
        assert {r.status for r in db.scalars(select(DataImportRow))} == {"valid"}
    monkeypatch.setattr(import_records, "insert_records", real)
    assert run(api, business, import_id).json()["created"]["sales"] == 5  # and it can be retried


def test_a_clash_with_existing_data_is_reported_cleanly(api, db, business, monkeypatch):
    # Another request adds the same order between the final check and the insert.
    import_id = checked(api, business, csv(*ROWS))
    real = import_records.build

    def build_then_collide(dataset, resolver, outcomes):
        tables, targets = real(dataset, resolver, outcomes)
        db.add(
            Sale(
                sold_on=date(2026, 9, 28),
                net_amount=D("1"),
                vat_amount=D("0"),
                gross_amount=D("1"),
                source="csv",
                source_ref="1001",
                organization_id=uuid.UUID(business[0]),
            )
        )
        db.flush()
        return tables, targets

    monkeypatch.setattr(import_records, "build", build_then_collide)
    res = run(api, business, import_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "import_conflict"
    assert "uq_sales_source_ref" in res.json()["error"]["details"]["constraint"]
    with scoped(db, business):
        imp = db.get(DataImport, uuid.UUID(import_id), populate_existing=True)
        assert imp.status == "validated"


# --- undo ----------------------------------------


def test_undo_removes_everything_the_import_created(api, db, business):
    mapping = SALES_MAP | {"product": "Item", "customer_email": "Email", "channel": "Channel"}
    content = csv(
        "28/09/2026,1,12.00,2.00,Loaf,jo@example.com,Shop",
        "29/09/2026,2,24.00,4.00,Bun,sam@example.com,Shop",
        header="Date,Order,Total,VAT,Item,Email,Channel",
    )
    import_id = checked(api, business, content, mapping)
    run(api, business, import_id)
    with scoped(db, business):
        assert (count(db, Sale), count(db, SaleLine), count(db, Product), count(db, Customer)) == (
            2,
            2,
            2,
            2,
        )
    before = api.get(base(business, import_id) + "/records", headers=business[2]["owner"]).json()
    assert before["counts"] == {"sales": 2, "sale_lines": 2, "products": 2, "customers": 2}

    res = undo(api, business, import_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["removed"] == {"sales": 2, "sale_lines": 2, "products": 2, "customers": 2}
    assert body["data_import"]["status"] == "undone" and body["data_import"]["undone_at"]
    assert body["data_import"]["imported_at"]  # the history keeps both times
    with scoped(db, business):
        assert (count(db, Sale), count(db, SaleLine), count(db, Product), count(db, Customer)) == (
            0,
            0,
            0,
            0,
        )
        assert count(db, BusinessListItem) == 1  # the business's own "Shop" list entry stays
        assert {r.status for r in db.scalars(select(DataImportRow))} == {"valid"}
        assert all(r.target_id is None for r in db.scalars(select(DataImportRow)))
    assert (
        api.get(base(business, import_id) + "/records", headers=business[2]["owner"]).json()[
            "counts"
        ]
        == {}
    )
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "import.undone")).one()
    assert entry.details["removed"]["sales"] == 2


def test_undo_leaves_everything_else_alone(api, db, business):
    with scoped(db, business):
        db.add(
            Sale(
                sold_on=date(2026, 1, 1), net_amount=D("5"), vat_amount=D("1"), gross_amount=D("6")
            )
        )
        db.add(Customer(name="Old Friend"))
        db.flush()
    other = checked(api, business, csv("28/09/2026,1,12.00,2.00"), SALES_MAP)
    run(api, business, other)
    first = checked(api, business, csv(*ROWS))
    run(api, business, first)
    undo(api, business, first)
    with scoped(db, business):
        assert count(db, Sale) == 2  # the hand-entered one and the other import's
        assert count(db, Customer) == 1


def test_undoing_frees_the_orders_to_be_imported_again(api, db, business):
    first = checked(api, business, csv(*ROWS))
    run(api, business, first)
    undo(api, business, first)
    again = checked(api, business, csv(*ROWS))
    body = api.get(base(business, again) + "/validation", headers=business[2]["owner"]).json()
    assert (body["valid"], body["duplicate"]) == (3, 0)
    assert run(api, business, again).json()["created"] == {"sales": 3}


def test_undo_is_refused_when_later_data_depends_on_it(api, db, business):
    # First import creates the customer; a later import reuses her; undoing the first would
    # orphan the later sales.
    mapping = SALES_MAP | {"customer_email": "Email"}
    first = checked(
        api,
        business,
        csv("28/09/2026,1,12.00,2.00,jo@example.com", header="Date,Order,Total,VAT,Email"),
        mapping,
    )
    run(api, business, first)
    later = checked(
        api,
        business,
        csv("29/09/2026,2,12.00,2.00,jo@example.com", header="Date,Order,Total,VAT,Email"),
        mapping,
    )
    run(api, business, later)

    res = undo(api, business, first)
    assert res.status_code == 409 and res.json()["error"]["code"] == "undo_blocked"
    assert "later import" in res.json()["error"]["message"]
    with scoped(db, business):
        assert (count(db, Sale), count(db, Customer)) == (2, 1)  # nothing was removed
        assert db.get(DataImport, uuid.UUID(first), populate_existing=True).status == "imported"

    assert undo(api, business, later).status_code == 200  # in the right order it works...
    assert undo(api, business, first).status_code == 200
    with scoped(db, business):
        assert (count(db, Sale), count(db, Customer)) == (0, 0)


def test_hand_entered_data_can_also_block_an_undo(api, db, business):
    first = checked(
        api,
        business,
        csv("28/09/2026,1,12.00,2.00,jo@example.com", header="Date,Order,Total,VAT,Email"),
        SALES_MAP | {"customer_email": "Email"},
    )
    run(api, business, first)
    with scoped(db, business):
        customer = db.scalars(select(Customer)).one()
        db.add(
            Sale(
                sold_on=date(2026, 9, 30),
                net_amount=D("1"),
                vat_amount=D("0"),
                gross_amount=D("1"),
                customer_id=customer.id,
            )
        )
        db.flush()
    assert undo(api, business, first).status_code == 409


@pytest.mark.parametrize("stage", ["uploaded", "validated"])
def test_only_an_imported_import_can_be_undone(api, business, stage):
    import_id = checked(api, business, csv(*ROWS))
    res = undo(api, business, import_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_imported"


def test_it_cannot_be_undone_twice_or_imported_again(api, business):
    import_id = checked(api, business, csv(*ROWS))
    run(api, business, import_id)
    undo(api, business, import_id)
    again = undo(api, business, import_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_undone"
    redo = run(api, business, import_id)
    assert redo.status_code == 409 and redo.json()["error"]["code"] == "not_validated"


def test_an_imported_import_cannot_be_changed(api, business):
    import_id = checked(api, business, csv(*ROWS))
    run(api, business, import_id)
    url = base(business, import_id)
    headers = business[2]["owner"]
    assert (
        api.put(
            url + "/mapping", json={"mapping": SALES_MAP, "options": INC}, headers=headers
        ).status_code
        == 409
    )
    assert api.post(url + "/validate", headers=headers).status_code == 409
    assert api.patch(url, json={"header_row": 1}, headers=headers).status_code == 409


def test_the_row_lists_show_what_happened_after_importing(api, business):
    content = csv("28/09/2026,1,12.00,2.00", "31/04/2026,2,12.00,2.00")
    import_id = checked(api, business, content)
    run(api, business, import_id)
    headers = business[2]["owner"]
    imported = api.get(
        base(business, import_id) + "/rows", params={"status": "imported"}, headers=headers
    ).json()
    assert [r["row_number"] for r in imported["rows"]] == [2]
    problems = api.get(base(business, import_id) + "/problems.csv", headers=headers).content.decode(
        "utf-8-sig"
    )
    assert problems.count("\n") == 2 and "31/04/2026" in problems  # header + the one bad row


# --- history ----------------------------------------


def test_history_shows_who_and_what_happened(api, business):
    done = checked(api, business, csv(*ROWS))
    run(api, business, done)
    waiting = checked(api, business, csv("28/09/2026,9,5.00,1.00"))
    res = api.get(f"{ORGS}/{business[0]}/imports", headers=business[2]["owner"])
    listed = {i["id"]: i for i in res.json()}
    assert listed[done]["status"] == "imported" and listed[done]["imported_count"] == 3
    assert listed[waiting]["status"] == "validated"
    assert listed[done]["uploaded_by_name"] == "owner@acme.co.uk".split("@")[0]
    assert listed[done]["original_filename"] == "file.csv"


def test_history_can_be_filtered_and_paged(api, business):
    ids = [checked(api, business, csv(f"28/09/2026,{n},5.00,1.00")) for n in range(1, 4)]
    run(api, business, ids[0])
    headers = business[2]["owner"]
    url = f"{ORGS}/{business[0]}/imports"
    assert [
        i["id"] for i in api.get(url, params={"status": "imported"}, headers=headers).json()
    ] == [ids[0]]
    assert len(api.get(url, params={"status": "validated"}, headers=headers).json()) == 2
    assert len(api.get(url, params={"dataset": "sales"}, headers=headers).json()) == 3
    assert api.get(url, params={"dataset": "customers"}, headers=headers).json() == []
    assert len(api.get(url, params={"limit": 1}, headers=headers).json()) == 1
    assert len(api.get(url, params={"offset": 2}, headers=headers).json()) == 1
    for bad in ({"status": "done"}, {"dataset": "payroll"}, {"offset": -1}, {"limit": 0}):
        assert api.get(url, params=bad, headers=headers).status_code == 422


def test_the_detail_page_names_the_uploader(api, business):
    import_id = checked(api, business, csv(*ROWS))
    body = api.get(base(business, import_id), headers=business[2]["owner"]).json()
    assert body["uploaded_by_name"] == "owner"


# --- who may do this ----------------------------------------


def test_viewers_cannot_import_undo_or_see_records(api, business):
    import_id = checked(api, business, csv(*ROWS))
    assert run(api, business, import_id, who="viewer").status_code == 403
    run(api, business, import_id)
    assert undo(api, business, import_id, who="viewer").status_code == 403
    res = api.get(base(business, import_id) + "/records", headers=business[2]["viewer"])
    assert res.status_code == 403


def test_logging_in_is_required(api, business):
    import_id = checked(api, business, csv(*ROWS))
    assert api.post(base(business, import_id) + "/import").status_code == 401
    assert api.post(base(business, import_id) + "/undo").status_code == 401


def test_another_business_cannot_import_or_undo_this(api, db, business):
    import_id = checked(api, business, csv(*ROWS))
    for org in (0, 1):
        assert run(api, business, import_id, who="other", org=org).status_code == 404
        assert undo(api, business, import_id, who="other", org=org).status_code == 404
        res = api.get(base(business, import_id, org) + "/records", headers=business[2]["other"])
        assert res.status_code == 404
    with scoped(db, business):
        assert count(db, Sale) == 0


def test_each_business_keeps_its_own_data(api, db, business):
    run(api, business, checked(api, business, csv(*ROWS)))
    theirs = checked(api, business, csv(*ROWS), org=1, who="other")  # same order numbers
    run(api, business, theirs, who="other", org=1)
    with scoped(db, business, 0):
        assert count(db, Sale) == 3
    with scoped(db, business, 1):
        assert count(db, Sale) == 3


def test_a_sale_row_with_a_negative_quantity_still_gets_a_positive_line(api, db, business):
    mapping = SALES_MAP | {"quantity": "Qty"}
    content = csv("28/09/2026,1,12.00,2.00,-2", header="Date,Order,Total,VAT,Qty")
    run(api, business, checked(api, business, content, mapping))
    with scoped(db, business):
        assert db.scalars(select(SaleLine.quantity)).one() == D("2")


def test_a_row_that_can_no_longer_be_read_is_skipped_not_imported(api, db, business, monkeypatch):
    import_id = checked(api, business, csv(*ROWS))
    real = import_runner.parse_row

    def parse(dataset, raw, mapping, options, *, today=None):
        if raw["Order"] == "1002":  # e.g. a date that has since become "in the future"
            return RowOutcome(
                errors=[{"field": "sold_on", "code": "date_out_of_range", "message": "too late"}]
            )
        return real(dataset, raw, mapping, options, today=today)

    monkeypatch.setattr(import_runner, "parse_row", parse)
    body = run(api, business, import_id).json()
    assert body["created"] == {"sales": 2} and body["skipped_invalid"] == 1
    assert body["data_import"]["invalid_count"] == 1 and body["data_import"]["imported_count"] == 2
    with scoped(db, business):
        bad = db.scalars(select(DataImportRow).where(DataImportRow.status == "invalid")).one()
        assert bad.raw["Order"] == "1002" and bad.errors[0]["code"] == "date_out_of_range"


def test_every_line_an_import_creates_is_tied_to_that_import(api, db, business):
    mapping = SALES_MAP | {"product": "Item"}
    content = csv(
        "28/09/2026,1,12.00,2.00,Loaf",
        "29/09/2026,2,6.00,1.00,Bun",
        header="Date,Order,Total,VAT,Item",
    )
    import_id = checked(api, business, content, mapping)
    run(api, business, import_id)
    with scoped(db, business):
        lines = db.scalars(select(SaleLine)).all()
        assert len(lines) == 2 and {str(x.import_id) for x in lines} == {import_id}
        assert {x.import_id for x in lines} == {s.import_id for s in db.scalars(select(Sale))}


def test_statistics_are_refreshed_as_an_import_grows_and_before_an_undo(api, business, monkeypatch):
    calls = []
    real = import_runner.refresh_statistics
    monkeypatch.setattr(
        import_runner, "refresh_statistics", lambda db: (calls.append("analyze"), real(db))
    )
    import_id = checked(api, business, csv(*ROWS))
    run(api, business, import_id)
    assert len(calls) == 2  # after the first batch, and once it is all committed
    assert undo(api, business, import_id).status_code == 200
    assert len(calls) == 3  # before the undo's deletes


def test_a_long_import_is_replanned_at_batches_1_4_16_and_64(api, business, monkeypatch):
    assert [n for n in range(1, 70) if import_runner._grown_enough_to_replan(n)] == [1, 4, 16, 64]
    monkeypatch.setattr(import_runner, "BATCH", 1)
    calls = []
    real = import_runner.refresh_statistics
    monkeypatch.setattr(
        import_runner, "refresh_statistics", lambda db: (calls.append("analyze"), real(db))
    )
    rows = [f"28/09/2026,{n},10.00,0" for n in range(1, 7)]
    run(api, business, checked(api, business, csv(*rows)))
    assert len(calls) == 3  # after batches 1 and 4 of 6, and once at the end
