"""Which columns feed which fields: the catalogue, matching and mapping rules."""

import pytest

from app.models.imports import DATASETS
from app.services import import_fields as f
from app.services.import_fields import (
    DATASET_FIELDS,
    check_mapping,
    normalise,
    suggest_mapping,
    suggest_vat_inclusive,
)


def codes(issues):
    return sorted(i.code for i in issues)


# --- the catalogue is consistent --------------------------------------------------------------


def test_every_dataset_has_fields():
    assert set(DATASET_FIELDS) == set(DATASETS)
    assert set(f.REQUIRED) == set(DATASETS)


@pytest.mark.parametrize("dataset", DATASETS)
def test_field_keys_are_unique_and_requirements_refer_to_real_fields(dataset):
    keys = [fld.key for fld in DATASET_FIELDS[dataset]]
    assert len(keys) == len(set(keys))
    for key in f.REQUIRED[dataset]:
        assert key in keys
    for group in f.ONE_OF.get(dataset, ()):
        assert all(key in keys for key in group)
    if dataset in f.MONEY_AMOUNT_FIELD:
        assert f.MONEY_AMOUNT_FIELD[dataset] in keys
        assert all(key in keys for key in f.VAT_COLUMNS[dataset])


@pytest.mark.parametrize("dataset", DATASETS)
def test_aliases_are_written_the_way_they_are_matched(dataset):
    # An alias that isn't already normalised could never match anything.
    for fld in DATASET_FIELDS[dataset]:
        for alias in fld.aliases:
            assert alias == normalise(alias), (dataset, fld.key, alias)


@pytest.mark.parametrize("dataset", DATASETS)
def test_no_column_name_is_claimed_by_two_fields(dataset):
    claimed: dict[str, str] = {}
    for fld in DATASET_FIELDS[dataset]:
        for alias in fld.aliases:
            assert alias not in claimed, (
                f"{dataset}: '{alias}' is on {claimed[alias]} and {fld.key}"
            )
            claimed[alias] = fld.key


@pytest.mark.parametrize("dataset", DATASETS)
def test_every_field_is_explained_for_the_screen(dataset):
    for fld in DATASET_FIELDS[dataset]:
        assert fld.label and fld.help and fld.type


def test_vat_rates_are_the_uk_ones():
    assert f.VAT_RATES == ("0", "5", "20")


# --- matching headings ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Total (inc. VAT)", "total inc vat"),
        ("  ORDER   ID ", "order id"),
        ("Order_No.", "order no"),
        ("VAT %", "vat %"),
        ("Café", "caf"),
        ("", ""),
    ],
)
def test_normalise(heading, expected):
    assert normalise(heading) == expected


def test_a_typical_till_export_maps_itself():
    headers = ["Date", "Order ID", "Product", "Qty", "Total (inc VAT)", "VAT", "Channel"]
    assert suggest_mapping("sales", headers) == {
        "sold_on": "Date",
        "amount": "Total (inc VAT)",
        "vat_amount": "VAT",
        "reference": "Order ID",
        "product": "Product",
        "quantity": "Qty",
        "channel": "Channel",
    }


def test_headings_match_whatever_the_case_and_punctuation():
    got = suggest_mapping("sales", ["DATE OF SALE", "order_number", "TOTAL (INC. VAT)"])
    assert got == {
        "sold_on": "DATE OF SALE",
        "amount": "TOTAL (INC. VAT)",
        "reference": "order_number",
    }


def test_the_word_vat_inside_a_total_heading_does_not_make_it_the_vat_column():
    assert suggest_mapping("sales", ["Date", "Total (inc VAT)"]) == {
        "sold_on": "Date",
        "amount": "Total (inc VAT)",
    }


def test_unknown_headings_are_left_alone_rather_than_guessed():
    assert suggest_mapping("sales", ["Foo", "Bar", "Wibble"]) == {}


def test_a_column_feeds_only_one_field():
    # "description" is a product name for sales; "item" would be too: only one is taken.
    got = suggest_mapping("sales", ["Item", "Description"])
    assert got == {"product": "Item"}


def test_the_leftmost_matching_column_wins():
    got = suggest_mapping("sales", ["Date", "Net", "Total"])
    assert got["amount"] == "Net"


def test_expenses_and_other_datasets_have_their_own_names():
    assert suggest_mapping("expenses", ["Date", "Supplier", "Net", "VAT", "Category"]) == {
        "spent_on": "Date",
        "supplier": "Supplier",
        "amount": "Net",
        "vat_amount": "VAT",
        "category": "Category",
    }
    assert suggest_mapping("customers", ["Name", "Email Address", "Post Code"]) == {
        "name": "Name",
        "email": "Email Address",
        "postcode": "Post Code",
    }
    assert suggest_mapping("stock_movements", ["Date", "SKU", "Qty"]) == {
        "moved_on": "Date",
        "sku": "SKU",
        "quantity": "Qty",
    }


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Total (inc VAT)", True),
        ("Total including VAT", True),
        ("Gross", True),
        ("Total (ex VAT)", False),
        ("Net amount", False),
        ("Excl. VAT", False),
        ("Amount", None),
        ("Total", None),
        ("Gross net", None),  # contradictory: don't guess
        ("", None),
        (None, None),
    ],
)
def test_the_amount_heading_can_hint_whether_vat_is_included(heading, expected):
    assert suggest_vat_inclusive(heading) is expected


# --- checking a mapping ------------------------------------------------------------------------

HEADERS = ["Date", "Total", "VAT", "Order ID", "Customer"]
GOOD = {"sold_on": "Date", "amount": "Total", "vat_amount": "VAT"}


def test_a_complete_sales_mapping_is_ready():
    assert check_mapping("sales", GOOD, {"vat_inclusive": True}, HEADERS) == []


def test_required_fields_must_be_mapped():
    got = check_mapping("sales", {}, {}, HEADERS)
    assert codes(got) == ["required_field_missing"] * 2
    assert {i.field for i in got} == {"sold_on", "amount"}


def test_the_column_must_exist_in_the_file():
    got = check_mapping("sales", GOOD | {"sold_on": "Day"}, {"vat_inclusive": True}, HEADERS)
    assert codes(got) == ["unknown_column"]
    assert "Day" in got[0].message


def test_column_headings_must_match_exactly():
    got = check_mapping("sales", GOOD | {"sold_on": "date"}, {"vat_inclusive": True}, HEADERS)
    assert codes(got) == ["unknown_column"]


def test_unknown_field_is_refused():
    got = check_mapping("sales", GOOD | {"colour": "Date"}, {"vat_inclusive": True}, HEADERS)
    assert "unknown_field" in codes(got)


def test_one_column_cannot_feed_two_fields():
    mapping = GOOD | {"vat_amount": "Total"}
    got = check_mapping("sales", mapping, {"vat_inclusive": True}, HEADERS)
    assert codes(got) == ["column_used_twice"]


def test_vat_question_must_be_answered_for_money_files():
    got = check_mapping("sales", GOOD, {}, HEADERS)
    assert codes(got) == ["vat_inclusive_required"]
    assert got[0].field == "vat_inclusive"


def test_without_a_vat_column_the_rate_is_needed():
    mapping = {"sold_on": "Date", "amount": "Total"}
    assert codes(check_mapping("sales", mapping, {"vat_inclusive": True}, HEADERS)) == [
        "vat_rate_required"
    ]
    assert (
        check_mapping("sales", mapping, {"vat_inclusive": True, "default_vat_rate": "20"}, HEADERS)
        == []
    )
    # A zero-rated shop still has to say so.
    assert (
        check_mapping("sales", mapping, {"vat_inclusive": False, "default_vat_rate": "0"}, HEADERS)
        == []
    )


def test_a_per_row_vat_rate_column_is_enough():
    headers = ["Date", "Total", "Rate"]
    mapping = {"sold_on": "Date", "amount": "Total", "vat_rate": "Rate"}
    assert check_mapping("sales", mapping, {"vat_inclusive": True}, headers) == []


def test_only_uk_vat_rates_are_accepted():
    for bad in ("17.5", "15", "twenty", "-5"):
        got = check_mapping(
            "sales", GOOD, {"vat_inclusive": True, "default_vat_rate": bad}, HEADERS
        )
        assert codes(got) == ["bad_vat_rate"], bad


def test_unknown_options_are_refused():
    got = check_mapping("sales", GOOD, {"vat_inclusive": True, "round_up": True}, HEADERS)
    assert codes(got) == ["unknown_option"]


def test_datasets_without_money_take_no_options():
    got = check_mapping("suppliers", {"name": "Customer"}, {"vat_inclusive": True}, HEADERS)
    assert codes(got) == ["options_not_applicable"]


def test_vat_questions_wait_until_the_amount_is_mapped():
    got = check_mapping("sales", {"sold_on": "Date"}, {}, HEADERS)
    assert codes(got) == ["required_field_missing"]  # not also the VAT questions


@pytest.mark.parametrize(
    ("dataset", "mapping", "expected"),
    [
        ("customers", {}, ["one_of_missing"]),
        ("customers", {"email": "Customer"}, []),
        ("customers", {"name": "Customer"}, []),
        ("suppliers", {}, ["required_field_missing"]),
        ("suppliers", {"name": "Customer"}, []),
        ("products", {}, ["required_field_missing"]),
        ("stock_movements", {"moved_on": "Date", "quantity": "Total"}, ["one_of_missing"]),
        ("stock_movements", {"moved_on": "Date", "quantity": "Total", "sku": "Order ID"}, []),
        ("stock_movements", {"moved_on": "Date", "quantity": "Total", "product": "Customer"}, []),
        ("stock_movements", {"sku": "Order ID"}, ["required_field_missing"] * 2),
    ],
)
def test_requirements_for_other_datasets(dataset, mapping, expected):
    assert codes(check_mapping(dataset, mapping, {}, HEADERS)) == expected


def test_products_need_the_vat_answer_only_when_a_price_is_mapped():
    assert check_mapping("products", {"name": "Customer"}, {}, HEADERS) == []
    got = check_mapping("products", {"name": "Customer", "unit_price": "Total"}, {}, HEADERS)
    assert codes(got) == ["vat_inclusive_required", "vat_rate_required"]


def test_answers_can_be_deferred_when_only_the_shape_is_wanted():
    assert check_mapping("sales", GOOD, {}, HEADERS, require_answers=False) == []
