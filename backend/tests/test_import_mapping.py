"""Column mapping: GET/PUT /organizations/{id}/imports/{id}/mapping and saved sources."""

import io
import uuid
from datetime import UTC, datetime

import openpyxl
import pytest
from sqlalchemy import select

from app.db.tenant import tenant_scope
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.models.imports import DataImport, DataSource

ORGS = "/api/v1/organizations"
TILL = (
    "Date,Order ID,Product,Qty,Total (inc VAT),VAT\n"
    "28/09/2026,1001,Sourdough,2,£9.00,£1.50\n"
    "29/09/2026,1002,Croissant,1,£2.20,£0.37\n"
).encode()
GOOD = {
    "sold_on": "Date",
    "amount": "Total (inc VAT)",
    "vat_amount": "VAT",
    "reference": "Order ID",
}


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


def upload(api, business, content=TILL, name="till.csv", dataset="sales", org=0, who="owner"):
    res = api.post(
        f"{ORGS}/{business[org]}/imports",
        files={"file": (name, content)},
        data={"dataset": dataset},
        headers=business[2][who],
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def url(business, import_id, org=0):
    return f"{ORGS}/{business[org]}/imports/{import_id}/mapping"


def get_mapping(api, business, import_id, who="owner", org=0):
    return api.get(url(business, import_id, org), headers=business[2][who])


def put_mapping(api, business, import_id, mapping, options=None, who="owner", org=0, **extra):
    body = {
        "mapping": mapping,
        "options": options if options is not None else {"vat_inclusive": True},
    }
    return api.put(url(business, import_id, org), json=body | extra, headers=business[2][who])


def xlsx(sheets):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def record(db, business, import_id):
    with tenant_scope(db, uuid.UUID(business[0])):
        return db.get(DataImport, uuid.UUID(import_id), populate_existing=True)


# --- what the screen is shown ----------------------------------------


def test_a_fresh_upload_comes_with_suggestions(api, business):
    body = get_mapping(api, business, upload(api, business)).json()
    assert body["dataset"] == "sales" and body["status"] == "uploaded"
    assert body["headers"] == ["Date", "Order ID", "Product", "Qty", "Total (inc VAT)", "VAT"]
    assert body["suggested_mapping"] == {
        "sold_on": "Date",
        "amount": "Total (inc VAT)",
        "vat_amount": "VAT",
        "reference": "Order ID",
        "product": "Product",
        "quantity": "Qty",
    }
    assert body["suggestion_from"] == "automatic" and body["saved_source"] is None
    assert body["suggested_options"] == {"vat_inclusive": True, "default_vat_rate": None}
    assert body["mapping"] == {} and body["options"] == {}
    assert body["needs_vat_options"] is True and body["vat_rates"] == ["0", "5", "20"]


def test_the_fields_say_what_is_required(api, business):
    body = get_mapping(api, business, upload(api, business)).json()
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["sold_on"]["required"] and fields["amount"]["required"]
    assert not fields["notes"]["required"]
    assert fields["sold_on"]["label"] == "Date of sale" and fields["sold_on"]["type"] == "date"
    assert [f["key"] for f in body["fields"]][:2] == ["sold_on", "amount"]


def test_nothing_is_ready_until_a_mapping_is_saved(api, business):
    body = get_mapping(api, business, upload(api, business)).json()
    assert body["ready"] is False
    assert {i["field"] for i in body["issues"]} == {"sold_on", "amount"}


def test_unrecognised_columns_get_no_suggestion(api, business):
    body = get_mapping(api, business, upload(api, business, b"Foo,Bar\n1,2\n")).json()
    assert body["suggested_mapping"] == {} and body["suggestion_from"] is None


@pytest.mark.parametrize(
    ("heading", "dataset", "expected"),
    [
        ("Total (ex VAT)", "sales", False),
        ("Total (inc VAT)", "sales", True),
        ("Amount", "sales", True),  # sales files are usually takings, which include VAT
        ("Amount", "expenses", None),  # no assumption for costs
    ],
)
def test_the_vat_question_is_pre_answered_only_when_that_is_safe(
    api, business, heading, dataset, expected
):
    date = "Date" if dataset == "sales" else "Date"
    content = f"{date},{heading}\n01/09/2026,10\n".encode()
    got = get_mapping(api, business, upload(api, business, content, dataset=dataset)).json()
    assert got["suggested_options"]["vat_inclusive"] is expected


def test_other_datasets_have_their_own_fields(api, business):
    csv = b"Name,Email,Postcode\nJo,jo@example.com,LS1 4AP\n"
    body = get_mapping(api, business, upload(api, business, csv, dataset="customers")).json()
    assert body["needs_vat_options"] is False
    assert body["suggested_mapping"] == {"name": "Name", "email": "Email", "postcode": "Postcode"}
    name = next(f for f in body["fields"] if f["key"] == "name")
    assert name["one_of"] == ["name", "email"] and not name["required"]


# --- saving a mapping ----------------------------------------


def test_save_a_complete_mapping(api, db, business):
    import_id = upload(api, business)
    res = put_mapping(api, business, import_id, GOOD)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "mapped" and body["ready"] is True and body["issues"] == []
    assert body["mapping"] == GOOD and body["options"] == {"vat_inclusive": True}
    saved = record(db, business, import_id)
    assert saved.column_mapping == GOOD and saved.status == "mapped"
    assert saved.options == {"vat_inclusive": True}
    assert get_mapping(api, business, import_id).json()["mapping"] == GOOD


def test_saving_is_audited(api, db, business):
    import_id = upload(api, business)
    put_mapping(api, business, import_id, GOOD, save_as="Till export")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "import.mapped")).one()
    assert entry.target_id == import_id
    assert entry.details["saved_as"] == "Till export"
    assert entry.details["fields"] == sorted(GOOD)


def test_blank_and_null_entries_mean_unmapped(api, business):
    import_id = upload(api, business)
    res = put_mapping(
        api, business, import_id, GOOD | {"product": None, "quantity": "  ", "sku": ""}
    )
    assert res.status_code == 200, res.text
    assert res.json()["mapping"] == GOOD


def test_an_incomplete_mapping_is_refused_and_nothing_is_saved(api, db, business):
    import_id = upload(api, business)
    res = put_mapping(api, business, import_id, {"sold_on": "Date"})
    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "mapping_invalid"
    assert [d["code"] for d in error["details"]] == ["required_field_missing"]
    assert error["details"][0]["field"] == "amount"
    saved = record(db, business, import_id)
    assert saved.column_mapping == {} and saved.status == "uploaded"


@pytest.mark.parametrize(
    ("mapping", "options", "code"),
    [
        (GOOD | {"sold_on": "Day"}, None, "unknown_column"),
        (GOOD | {"sold_on": "date"}, None, "unknown_column"),
        (GOOD | {"colour": "Date"}, None, "unknown_field"),
        (GOOD | {"vat_amount": "Total (inc VAT)"}, None, "column_used_twice"),
        (GOOD, {}, "vat_inclusive_required"),
        (
            {"sold_on": "Date", "amount": "Total (inc VAT)"},
            {"vat_inclusive": True},
            "vat_rate_required",
        ),
    ],
)
def test_each_kind_of_mapping_problem_is_reported(api, business, mapping, options, code):
    res = put_mapping(api, business, upload(api, business), mapping, options)
    assert res.status_code == 422
    assert code in [d["code"] for d in res.json()["error"]["details"]]


def test_a_vat_rate_stands_in_for_a_missing_vat_column(api, business):
    mapping = {"sold_on": "Date", "amount": "Total (inc VAT)"}
    res = put_mapping(
        api,
        business,
        upload(api, business),
        mapping,
        {"vat_inclusive": True, "default_vat_rate": "20"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["options"] == {"vat_inclusive": True, "default_vat_rate": "20"}


def test_only_uk_vat_rates_are_taken(api, business):
    res = put_mapping(
        api,
        business,
        upload(api, business),
        GOOD,
        {"vat_inclusive": True, "default_vat_rate": "17.5"},
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "validation_error"


def test_unexpected_request_fields_are_refused(api, business):
    import_id = upload(api, business)
    assert put_mapping(api, business, import_id, GOOD, colour="red").status_code == 422
    res = put_mapping(api, business, import_id, GOOD, options={"vat_inclusive": True, "x": 1})
    assert res.status_code == 422


def test_remapping_replaces_the_old_mapping_and_resets_validation(api, db, business):
    import_id = upload(api, business)
    put_mapping(api, business, import_id, GOOD)
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(import_id))
        imp.status, imp.valid_count, imp.invalid_count = "validated", 1, 1
        db.flush()
    other = {"sold_on": "Date", "amount": "Total (inc VAT)", "vat_amount": "VAT"}
    assert put_mapping(api, business, import_id, other, {"vat_inclusive": False}).status_code == 200
    saved = record(db, business, import_id)
    assert saved.status == "mapped" and saved.column_mapping == other
    assert (saved.valid_count, saved.invalid_count) == (0, 0)
    assert saved.options == {"vat_inclusive": False}


def test_a_finished_import_cannot_be_remapped(api, db, business):
    import_id = upload(api, business)
    put_mapping(api, business, import_id, GOOD)
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(import_id))
        imp.status, imp.imported_at = "imported", datetime.now(UTC)
        db.flush()
    res = put_mapping(api, business, import_id, GOOD)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_editable"


NET = {"vat_inclusive": False}


@pytest.mark.parametrize(
    ("dataset", "csv", "mapping", "options"),
    [
        (
            "expenses",
            b"Date,Supplier,Net,VAT\n1/9/2026,Flour Co,10,2\n",
            None,
            {"vat_inclusive": False},
        ),
        ("customers", b"Name,Email\nJo,jo@example.com\n", {"email": "Email"}, {}),
        ("suppliers", b"Name\nFlour Co\n", {"name": "Name"}, {}),
        ("products", b"Name,SKU\nLoaf,SD1\n", {"name": "Name", "sku": "SKU"}, {}),
        ("stock_movements", b"Date,SKU,Qty\n1/9/2026,SD1,5\n", None, {}),
    ],
)
def test_every_dataset_can_be_mapped(api, business, dataset, csv, mapping, options):
    import_id = upload(api, business, csv, dataset=dataset)
    if mapping is None:
        mapping = get_mapping(api, business, import_id).json()["suggested_mapping"]
    res = put_mapping(api, business, import_id, mapping, options)
    assert res.status_code == 200, res.text
    assert res.json()["ready"] is True


def test_options_are_refused_for_datasets_without_money(api, business):
    import_id = upload(api, business, b"Name\nFlour Co\n", dataset="suppliers")
    res = put_mapping(api, business, import_id, {"name": "Name"}, {"vat_inclusive": True})
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["code"] == "options_not_applicable"


# --- Excel sheets ---------------------------------------------------------------------------------


def test_a_workbook_needs_its_sheet_chosen_first(api, business):
    book = xlsx({"Notes": [["Note"], ["hi"]], "Sales": [["Date", "Total"], ["1/9/2026", 5]]})
    import_id = upload(api, business, book, name="book.xlsx")
    res = get_mapping(api, business, import_id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "choose_sheet_first"
    assert put_mapping(api, business, import_id, GOOD).status_code == 409

    api.patch(
        f"{ORGS}/{business[0]}/imports/{import_id}",
        json={"sheet_name": "Sales"},
        headers=business[2]["owner"],
    )
    assert get_mapping(api, business, import_id).json()["headers"] == ["Date", "Total"]


def test_the_original_file_must_still_exist(api, db, business, storage):
    import_id = upload(api, business)
    with tenant_scope(db, uuid.UUID(business[0])):
        imp = db.get(DataImport, uuid.UUID(import_id))
        storage.delete(imp.storage_key)
        imp.file_deleted_at = datetime.now(UTC)
        db.flush()
    for res in (get_mapping(api, business, import_id), put_mapping(api, business, import_id, GOOD)):
        assert res.status_code == 409 and res.json()["error"]["code"] == "file_deleted"


# --- remembering mappings ----------------------------------------


def test_save_a_mapping_under_a_name(api, db, business):
    import_id = upload(api, business)
    res = put_mapping(api, business, import_id, GOOD, save_as="  Till export ")
    assert res.status_code == 200, res.text
    listed = api.get(f"{ORGS}/{business[0]}/data-sources", headers=business[2]["owner"]).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Till export" and listed[0]["dataset"] == "sales"
    assert listed[0]["kind"] == "csv" and listed[0]["column_mapping"] == GOOD
    assert listed[0]["options"] == {"vat_inclusive": True}
    assert record(db, business, import_id).data_source_id == uuid.UUID(listed[0]["id"])


def test_the_next_file_from_the_same_place_maps_itself(api, business):
    put_mapping(api, business, upload(api, business), GOOD, {"vat_inclusive": True}, save_as="Till")
    # A new export: different order, different capitalisation, extra column.
    again = b"Extra,TOTAL (INC. VAT),date,order id,Vat\nx,5.00,01/10/2026,2001,0.83\n"
    body = get_mapping(api, business, upload(api, business, again, name="oct.csv")).json()
    assert body["suggestion_from"] == "saved" and body["saved_source"]["name"] == "Till"
    assert body["suggested_mapping"] == {
        "sold_on": "date",
        "amount": "TOTAL (INC. VAT)",
        "vat_amount": "Vat",
        "reference": "order id",
    }
    assert body["suggested_options"]["vat_inclusive"] is True


def test_a_saved_mapping_is_not_offered_when_the_file_lacks_its_columns(api, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Till")
    other = b"Date,Total\n01/10/2026,5\n"
    body = get_mapping(api, business, upload(api, business, other, name="o.csv")).json()
    assert body["suggestion_from"] == "automatic" and body["saved_source"] is None
    assert body["suggested_mapping"] == {"sold_on": "Date", "amount": "Total"}


def test_saved_mappings_are_kept_per_dataset(api, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Till")
    expenses = b"Date,Total (inc VAT),VAT,Order ID\n01/10/2026,5,0.83,9\n"
    body = get_mapping(api, business, upload(api, business, expenses, dataset="expenses")).json()
    assert body["saved_source"] is None


def test_the_most_recently_used_matching_source_is_preferred(api, db, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Alpha")
    second = upload(api, business, TILL + b"30/09/2026,1003,Bun,1,\xc2\xa31.00,\xc2\xa30.17\n")
    put_mapping(api, business, second, GOOD, save_as="Zulu")
    with tenant_scope(db, uuid.UUID(business[0])):
        for name, hour in (("Alpha", 9), ("Zulu", 10)):
            source = db.scalars(select(DataSource).where(DataSource.name == name)).one()
            source.last_imported_at = datetime(2026, 9, 29, hour, tzinfo=UTC)
        db.flush()
    body = get_mapping(api, business, upload(api, business, TILL.replace(b"1001", b"7"))).json()
    assert body["saved_source"]["name"] == "Zulu"  # most recently used, not alphabetical


def test_saving_under_an_existing_name_updates_it(api, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Till")
    smaller = {"sold_on": "Date", "amount": "Total (inc VAT)", "vat_amount": "VAT"}
    put_mapping(api, business, upload(api, business, TILL + b"\n"), smaller, save_as="Till")
    listed = api.get(f"{ORGS}/{business[0]}/data-sources", headers=business[2]["owner"]).json()
    assert len(listed) == 1 and listed[0]["column_mapping"] == smaller


def test_a_name_cannot_be_reused_for_another_kind_of_file(api, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Till")
    expenses = upload(api, business, b"Date,Total,VAT\n01/10/2026,5,1\n", dataset="expenses")
    mapping = {"spent_on": "Date", "amount": "Total", "vat_amount": "VAT"}
    res = put_mapping(api, business, expenses, mapping, save_as="Till")
    assert res.status_code == 409 and res.json()["error"]["code"] == "source_dataset_mismatch"


def test_a_failed_save_does_not_remember_anything(api, business):
    res = put_mapping(api, business, upload(api, business), {"sold_on": "Date"}, save_as="Till")
    assert res.status_code == 422
    assert api.get(f"{ORGS}/{business[0]}/data-sources", headers=business[2]["owner"]).json() == []


def test_saved_mappings_belong_to_one_business(api, business):
    put_mapping(api, business, upload(api, business), GOOD, save_as="Till")
    theirs = api.get(f"{ORGS}/{business[1]}/data-sources", headers=business[2]["other"])
    assert theirs.json() == []
    body = get_mapping(api, business, upload(api, business, org=1, who="other"), "other", 1).json()
    assert body["saved_source"] is None
    # ...and they can use the same name for their own.
    other_import = upload(api, business, org=1, who="other")
    assert (
        put_mapping(
            api, business, other_import, GOOD, org=1, who="other", save_as="Till"
        ).status_code
        == 200
    )


# --- who may do this ----------------------------------------


def test_viewers_cannot_map_or_list(api, business):
    import_id = upload(api, business)
    assert get_mapping(api, business, import_id, "viewer").status_code == 403
    assert put_mapping(api, business, import_id, GOOD, who="viewer").status_code == 403
    res = api.get(f"{ORGS}/{business[0]}/data-sources", headers=business[2]["viewer"])
    assert res.status_code == 403


def test_logging_in_is_required(api, business):
    import_id = upload(api, business)
    assert api.get(url(business, import_id)).status_code == 401
    assert api.put(url(business, import_id), json={"mapping": {}}).status_code == 401


def test_another_business_cannot_see_or_change_an_import_mapping(api, business):
    import_id = upload(api, business)
    assert get_mapping(api, business, import_id, "other", 0).status_code == 404
    assert put_mapping(api, business, import_id, GOOD, who="other", org=0).status_code == 404
    # Even addressing its own organisation, another business's import id is "not found".
    assert get_mapping(api, business, import_id, "other", 1).status_code == 404
    assert put_mapping(api, business, import_id, GOOD, who="other", org=1).status_code == 404


def test_an_unknown_import_is_not_found(api, business):
    assert get_mapping(api, business, uuid.uuid4()).status_code == 404


def test_a_saved_mapping_is_only_offered_for_the_same_kind_of_file(api, business):
    # "name" is a valid field for suppliers and customers alike, so only the dataset
    # can tell these apart.
    suppliers = upload(api, business, b"Name\nFlour Co\n", dataset="suppliers")
    put_mapping(api, business, suppliers, {"name": "Name"}, {}, save_as="Suppliers list")
    customers = upload(api, business, b"Name,Email\nJo,jo@example.com\n", dataset="customers")
    body = get_mapping(api, business, customers).json()
    assert body["saved_source"] is None and body["suggestion_from"] == "automatic"


def test_an_incomplete_saved_mapping_is_never_offered(api, db, business):
    with tenant_scope(db, uuid.UUID(business[0])):
        db.add(
            DataSource(
                name="Half done",
                kind="csv",
                dataset="sales",
                column_mapping={"sold_on": "Date"},  # no amount
                options={"vat_inclusive": True},
            )
        )
        db.flush()
    body = get_mapping(api, business, upload(api, business)).json()
    assert body["saved_source"] is None and body["suggestion_from"] == "automatic"
