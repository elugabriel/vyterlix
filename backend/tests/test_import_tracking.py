"""Phase 4 import tracking: the database keeps uploads, their rows and data-quality
issues consistent, separate between businesses, and linked to what they imported."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.tenant import TenantScopeError, tenant_scope
from app.models.data import Customer, Expense, Product, Sale, StockMovement, Supplier
from app.models.identity import Organization, User
from app.models.imports import DataImport, DataImportRow, DataQualityIssue, DataSource

D = Decimal
SHA = "a" * 64


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


def an_import(**kw):
    fields = {
        "source": "csv",
        "dataset": "sales",
        "original_filename": "till-export-september.csv",
        "storage_key": f"{uuid.uuid4()}/{uuid.uuid4()}.csv",
        "file_size_bytes": 1024,
        "file_sha256": SHA,
    }
    return DataImport(**(fields | kw))


def a_sale(**kw):
    fields = {
        "sold_on": date(2026, 9, 28),
        "net_amount": D("10"),
        "vat_amount": D("2"),
        "gross_amount": D("12"),
    }
    return Sale(**(fields | kw))


# --- separation between businesses ----------------------------------------------------------


@pytest.mark.parametrize("model", [DataSource, DataImport, DataImportRow, DataQualityIssue])
def test_import_tables_need_a_business_in_scope(db, model):
    with pytest.raises(TenantScopeError):
        db.execute(select(model))


def test_an_import_cannot_use_another_businesss_source(db, orgs):
    a, b = orgs
    with tenant_scope(db, b):
        theirs = saved(db, DataSource(name="Till", kind="csv", dataset="sales"))
    with tenant_scope(db, a):
        rejected(
            db, an_import(data_source_id=theirs.id), "fk_data_imports_organization_id_data_sources"
        )


def test_rows_and_issues_cannot_attach_to_another_businesss_import(db, orgs):
    a, b = orgs
    with tenant_scope(db, b):
        theirs = saved(db, an_import())
    with tenant_scope(db, a):
        rejected(
            db,
            DataImportRow(import_id=theirs.id, row_number=2, raw={}),
            "fk_data_import_rows_organization_id_data_imports",
        )
        rejected(
            db,
            DataQualityIssue(
                import_id=theirs.id, dataset="sales", issue_type="x", severity="info", message="m"
            ),
            "fk_data_quality_issues_organization_id_data_imports",
        )


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (Sale, {"sold_on": date(2026, 9, 28), "net_amount": 1, "gross_amount": 1}),
        (Expense, {"spent_on": date(2026, 9, 28), "net_amount": 1, "gross_amount": 1}),
        (Customer, {}),
        (Supplier, {"name": "Flour Co"}),
        (Product, {"name": "Loaf"}),
    ],
)
def test_records_cannot_claim_another_businesss_import(db, orgs, model, fields):
    a, b = orgs
    with tenant_scope(db, b):
        theirs = saved(db, an_import())
    table = model.__tablename__
    with tenant_scope(db, a):
        rejected(
            db, model(import_id=theirs.id, **fields), f"fk_{table}_organization_id_data_imports"
        )


def test_stock_movements_link_to_their_import(db, org_a):
    imp = saved(db, an_import(dataset="stock_movements"))
    product = saved(db, Product(name="Loaf"))
    move = saved(
        db,
        StockMovement(
            product_id=product.id,
            moved_on=date(2026, 9, 1),
            kind="opening",
            quantity=D("40"),
            import_id=imp.id,
        ),
    )
    assert move.import_id == imp.id


# --- undo support -------------------------------------------------------------------------------


def test_an_import_finds_exactly_the_records_it_created(db, org_a):
    first, second = saved(db, an_import()), saved(db, an_import())
    for imp, count in [(first, 3), (second, 2)]:
        for _ in range(count):
            saved(db, a_sale(source="csv", import_id=imp.id))
    saved(db, a_sale())  # typed in by hand: no import
    counted = db.scalar(select(func.count()).select_from(Sale).where(Sale.import_id == first.id))
    assert counted == 3


def test_an_import_cannot_be_deleted_while_its_records_exist(db, org_a):
    imp = saved(db, an_import())
    saved(db, a_sale(import_id=imp.id))
    with pytest.raises(IntegrityError, match="fk_sales_organization_id_data_imports"):
        with db.begin_nested():
            db.execute(delete(DataImport).where(DataImport.id == imp.id))


def test_deleting_an_import_removes_its_rows_and_issues(db, org_a):
    imp = saved(db, an_import())
    saved(db, DataImportRow(import_id=imp.id, row_number=2, raw={"Date": "28/09/2026"}))
    saved(
        db,
        DataQualityIssue(
            import_id=imp.id,
            dataset="sales",
            issue_type="missing_cost",
            severity="warning",
            message="12 sales have no cost of goods",
        ),
    )
    db.execute(delete(DataImport).where(DataImport.id == imp.id))
    assert db.scalar(select(func.count()).select_from(DataImportRow)) == 0
    assert db.scalar(select(func.count()).select_from(DataQualityIssue)) == 0


def test_a_business_wide_issue_needs_no_import(db, org_a):
    issue = saved(
        db,
        DataQualityIssue(
            dataset="sales",
            issue_type="missing_month",
            severity="critical",
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            message="No sales recorded for February 2026",
        ),
    )
    assert issue.import_id is None and issue.resolved_at is None


# --- import rules -------------------------------------------------------------------------------


def test_defaults_for_a_new_upload(db, org_a):
    imp = saved(db, an_import())
    assert imp.status == "uploaded"
    assert (imp.header_row, imp.row_count, imp.imported_count) == (1, 0, 0)
    assert imp.column_mapping == {} and imp.options == {}


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"source": "pdf"}, "source_valid"),
        ({"source": "xero"}, "source_valid"),  # connectors don't upload files
        ({"dataset": "payroll"}, "dataset_valid"),
        ({"status": "done"}, "status_valid"),
        ({"file_sha256": "ABC"}, "file_sha256_format"),
        ({"file_size_bytes": -1}, "file_size_not_negative"),
        ({"original_filename": "  "}, "filename_not_blank"),
        ({"sheet_name": "Sheet1"}, "sheet_only_for_excel"),
        ({"header_row": 0}, "header_row_positive"),
        ({"row_count": 5, "valid_count": 4, "invalid_count": 2}, "counts_add_up"),
        ({"invalid_count": -1}, "counts_not_negative"),
        ({"status": "imported"}, "imported_has_time"),
        ({"status": "undone", "imported_at": datetime.now(UTC)}, "undone_has_time"),
    ],
)
def test_import_rules(db, org_a, fields, constraint):
    rejected(db, an_import(**fields), constraint)


def test_an_excel_import_can_name_its_sheet(db, org_a):
    saved(db, an_import(source="excel", sheet_name="September", storage_key="k.xlsx"))


def test_an_undone_import_keeps_both_times(db, org_a):
    now = datetime.now(UTC)
    imp = saved(db, an_import(status="undone", imported_at=now, undone_at=now))
    assert imp.status == "undone"


def test_each_file_row_is_stored_once(db, org_a):
    imp = saved(db, an_import())
    saved(db, DataImportRow(import_id=imp.id, row_number=2, raw={}))
    rejected(db, DataImportRow(import_id=imp.id, row_number=2, raw={}), "uq_data_import_rows_row")


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"row_number": 0}, "row_number_positive"),
        ({"status": "maybe"}, "status_valid"),
        ({"status": "imported"}, "imported_has_target"),
        ({"target_table": "sales"}, "target_table_and_id_together"),
    ],
)
def test_import_row_rules(db, org_a, fields, constraint):
    imp = saved(db, an_import())
    rejected(
        db,
        DataImportRow(**({"import_id": imp.id, "row_number": 2, "raw": {}} | fields)),
        constraint,
    )


def test_an_imported_row_points_at_its_record(db, org_a):
    imp = saved(db, an_import())
    sale = saved(db, a_sale(import_id=imp.id))
    row = saved(
        db,
        DataImportRow(
            import_id=imp.id,
            row_number=2,
            raw={"Date": "28/09/2026", "Total": "£12.00"},
            status="imported",
            target_table="sales",
            target_id=sale.id,
        ),
    )
    assert row.errors == [] and row.raw["Total"] == "£12.00"


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"severity": "urgent"}, "severity_valid"),
        ({"issue_type": "Missing Month"}, "issue_type_format"),
        ({"affected_count": -1}, "affected_count_not_negative"),
        ({"period_start": date(2026, 3, 1), "period_end": date(2026, 2, 1)}, "period_in_order"),
        ({"dataset": "weather"}, "dataset_valid"),
    ],
)
def test_quality_issue_rules(db, org_a, fields, constraint):
    base = {"dataset": "sales", "issue_type": "missing_cost", "severity": "warning", "message": "m"}
    rejected(db, DataQualityIssue(**(base | fields)), constraint)


def test_source_names_unique_within_a_business_only(db, orgs):
    a, b = orgs
    with tenant_scope(db, a):
        saved(db, DataSource(name="Till export", kind="csv", dataset="sales"))
        rejected(
            db,
            DataSource(name="Till export", kind="excel", dataset="sales"),
            "uq_data_sources_name",
        )
    with tenant_scope(db, b):
        saved(db, DataSource(name="Till export", kind="csv", dataset="sales"))


def test_a_source_remembers_its_column_mapping(db, org_a):
    mapping = {"sold_on": "Date", "gross_amount": "Total (inc VAT)"}
    source = saved(db, DataSource(name="Till", kind="csv", dataset="sales", column_mapping=mapping))
    imp = saved(db, an_import(data_source_id=source.id, column_mapping=mapping))
    assert imp.column_mapping == source.column_mapping == mapping


def test_the_import_history_survives_the_uploader_being_deleted(db, org_a):
    user = saved(db, User(email="jo@acme.co.uk", password_hash="x", full_name="Jo"))
    imp = saved(db, an_import(uploaded_by_user_id=user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.refresh(imp)
    assert imp.uploaded_by_user_id is None


def test_same_file_uploaded_twice_is_findable(db, org_a):
    saved(db, an_import(created_at=datetime.now(UTC) - timedelta(days=3)))
    saved(db, an_import())
    assert db.scalar(select(func.count()).where(DataImport.file_sha256 == SHA)) == 2
