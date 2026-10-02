"""GET /data-quality, POST /data-quality/refresh and GET /data-quality/issues, on real records."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.db.tenant import tenant_scope
from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement
from app.models.identity import AuditLog, OrganizationUser, Role, User
from app.models.imports import DataImport

ORGS = "/api/v1/organizations"
D = Decimal
AS_OF = "2026-10-02"
SHA = "a" * 64


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


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def get(api, business, path="", who="owner", org=0, **params):
    url = f"{ORGS}/{business[org]}/data-quality{path}"
    return api.get(url, params=params or None, headers=business[2][who])


def refresh(api, business, who="owner", org=0, **params):
    url = f"{ORGS}/{business[org]}/data-quality/refresh"
    return api.post(url, params=params or None, headers=business[2][who])


def report(api, business, **params):
    res = get(api, business, as_of=AS_OF, **params)
    assert res.status_code == 200, res.text
    return res.json()


def add_sale(
    db, business, day, net="10.00", cost=None, line=True, customer=None, kind="sale", org=0
):
    with scoped(db, business, org):
        value = D(net) * (-1 if kind == "refund" else 1)
        sale = Sale(
            sold_on=day,
            kind=kind,
            net_amount=value,
            vat_amount=value * D("0.2"),
            gross_amount=value * D("1.2"),
            customer_id=customer,
        )
        db.add(sale)
        db.flush()
        if line:
            db.add(
                SaleLine(
                    sale_id=sale.id,
                    quantity=D("1") if kind == "sale" else D("-1"),
                    net_amount=value,
                    vat_amount=value * D("0.2"),
                    cost_amount=D(cost) if cost is not None else None,
                )
            )
            db.flush()
        return sale.id


def add_expense(db, business, day, gross="12.00", category=None, org=0):
    with scoped(db, business, org):
        value = D(gross)
        db.add(
            Expense(
                spent_on=day,
                net_amount=value / D("1.2"),
                vat_amount=value - value / D("1.2"),
                gross_amount=value,
                cost_category_id=category,
            )
        )
        db.flush()


def add_item(db, business, kind, name):
    with scoped(db, business):
        item = BusinessListItem(kind=kind, name=name)
        db.add(item)
        db.flush()
        return item.id


def component(body, dataset, key):
    [ds] = [d for d in body["datasets"] if d["dataset"] == dataset]
    return next(c for c in ds["components"] if c["key"] == key)


def issues(body, issue_type=None, dataset=None):
    return [
        i
        for i in body["issues"]
        if issue_type in (None, i["issue_type"]) and dataset in (None, i["dataset"])
    ]


# --- an empty business ------------------------------------------------


def test_a_business_with_no_data(api, business):
    body = report(api, business)
    assert body["score"] is None and body["band"] is None
    assert body["headline"] == "Add some sales to get a data-quality score."
    assert body["as_of"] == AS_OF and body["datasets"] == [] and body["months"] == []
    assert [i["issue_type"] for i in body["issues"]] == ["no_data"]


def test_as_of_defaults_to_today_in_the_uk(api, business):
    from app.core.uk import today_uk

    assert get(api, business).json()["as_of"] == today_uk().isoformat()


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "02/10/2026"])
def test_a_bad_as_of_is_refused(api, business, value):
    assert get(api, business, as_of=value).status_code == 422


# --- figures read from real records ------------------------------------------------


def test_costs_are_weighted_by_sales_value_and_refunds_count(api, db, business):
    add_sale(db, business, date(2026, 9, 10), net="100.00", cost="40.00")  # costed
    add_sale(db, business, date(2026, 9, 11), net="100.00", line=False)  # no lines at all
    add_sale(db, business, date(2026, 9, 12), net="50.00", cost="20.00", kind="refund")  # |-50|
    add_sale(db, business, date(2026, 9, 13), net="50.00", cost=None)  # a line, no cost
    body = report(api, business)
    # costed: 100 + 50 = 150 of 300 in total
    assert component(body, "sales", "cost_of_goods")["score"] == 50
    assert component(body, "sales", "cost_of_goods")["detail"] == (
        "50% of sales value has a cost of goods recorded"
    )
    assert component(body, "sales", "line_detail")["detail"] == "3 of 4 sales say what was sold"
    [cost] = issues(body, "missing_cost")
    assert (
        cost["severity"] == "warning"
        and cost["affected_count"] == 2
        and cost["details"] == {"cost_known_pct": 50}
    )


def test_expenses_are_weighted_by_value(api, db, business):
    stock = add_item(db, business, "cost_category", "Stock")
    add_expense(db, business, date(2026, 9, 3), gross="90.00", category=stock)
    add_expense(db, business, date(2026, 9, 4), gross="10.00")
    add_sale(db, business, date(2026, 9, 5), cost="1.00")
    body = report(api, business)
    assert component(body, "expenses", "categorised")["score"] == 90
    [found] = issues(body, "uncategorised_expenses")
    assert found["severity"] == "info" and found["affected_count"] == 1


def test_the_month_table_comes_from_the_records(api, db, business):
    add_sale(db, business, date(2026, 7, 5), cost="4.00")
    add_sale(db, business, date(2026, 7, 6), cost="4.00")
    add_sale(db, business, date(2026, 9, 28))
    add_expense(db, business, date(2026, 7, 9))
    body = report(api, business)
    rows = {m["label"]: m for m in body["months"]}
    assert list(rows) == ["September 2026", "August 2026", "July 2026"]
    assert (rows["July 2026"]["sales_records"], rows["July 2026"]["expenses_records"]) == (2, 1)
    assert rows["July 2026"]["cost_coverage_pct"] == 100
    assert rows["September 2026"]["cost_coverage_pct"] == 0
    assert rows["August 2026"]["sales_records"] == 0
    [gap] = issues(body, "missing_period", "sales")
    assert gap["message"] == "No sales are recorded for August 2026."


def test_dates_come_through_in_the_right_month_at_month_edges(api, db, business):
    for day in (date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 30)):
        add_sale(db, business, day, cost="1.00")
    rows = {m["label"]: m["sales_records"] for m in report(api, business)["months"]}
    assert rows == {"September 2026": 2, "August 2026": 2}


def test_other_businesses_do_not_affect_the_score(api, db, business):
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    for month in range(1, 10):
        add_sale(db, business, date(2026, month, 5), org=1)
        add_expense(db, business, date(2026, month, 5), org=1)
    mine = report(api, business)
    assert [d["records"] for d in mine["datasets"]] == [1]
    theirs = get(api, business, org=1, who="other", as_of=AS_OF).json()
    assert [d["records"] for d in theirs["datasets"]] == [9, 9]


def test_customers_products_and_stock_are_checked(api, db, business):
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    with scoped(db, business):
        db.add_all(
            [
                Customer(name="Jo", email="jo@example.com"),
                Customer(name="Jo B", email="jo@example.com"),  # same email
                Customer(name="Sam Smith", postcode="LS1 4AP"),
                Customer(name="sam smith", postcode="ls1 4ap".upper()),  # same name + postcode
                Customer(name="Pat"),
            ]
        )
        bun = Product(name="Bun", unit_cost=D("0.3"), unit_price_ex_vat=D("1"))
        db.add_all([bun, Product(name="BUN"), Product(name="Loaf", unit_price_ex_vat=D("4"))])
        db.flush()
        db.add(
            StockMovement(
                product_id=bun.id, moved_on=date(2026, 9, 1), kind="delivery", quantity=D("2")
            )
        )
        db.add(
            StockMovement(
                product_id=bun.id, moved_on=date(2026, 9, 2), kind="sale", quantity=D("-5")
            )
        )
        db.flush()  # while the business is still in scope, so the row gets its business
    body = report(api, business)
    assert (
        component(body, "customers", "duplicates")["detail"]
        == "2 possible duplicates among 5 customers"
    )
    assert issues(body, "duplicate_customers")[0]["affected_count"] == 2
    assert component(body, "products", "unit_cost")["detail"] == "1 of 3 products have a cost price"
    assert (
        component(body, "products", "unit_price")["detail"]
        == "2 of 3 products have a selling price"
    )
    assert issues(body, "duplicate_products")[0]["affected_count"] == 1
    [negative] = issues(body, "negative_stock")
    assert negative["affected_count"] == 1 and "Bun" in negative["message"]
    assert negative["details"]["products"][0]["quantity"] == "-3.0000"
    assert [d["dataset"] for d in body["datasets"]] == [
        "sales",
        "customers",
        "products",
        "stock_movements",
    ]


def test_how_recent_imports_went(api, db, business):
    def an_import(name, imported, invalid, status="imported"):
        with scoped(db, business):
            row = DataImport(
                source="csv",
                dataset="sales",
                status=status,
                original_filename=name,
                storage_key=f"{uuid.uuid4()}/{uuid.uuid4()}.csv",
                file_size_bytes=100,
                file_sha256=SHA,
                row_count=imported + invalid,
                valid_count=imported,
                invalid_count=invalid,
                imported_count=imported,
                imported_at=datetime(2026, 9, 12, 10, tzinfo=UTC),
                undone_at=datetime(2026, 9, 13, tzinfo=UTC) if status == "undone" else None,
            )
            db.add(row)
            db.flush()
            return str(row.id)

    messy = an_import("messy.csv", 60, 40)
    an_import("clean.csv", 100, 0)
    an_import("undone.csv", 1, 99, status="undone")  # not in the data any more
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    body = report(api, business)
    assert {i["filename"]: i["clean_rate_pct"] for i in body["imports"]} == {
        "messy.csv": 60,
        "clean.csv": 100,
    }
    [bad] = issues(body, "rows_with_problems")
    assert bad["import_id"] == messy and bad["severity"] == "critical"
    assert (
        bad["message"] == "'messy.csv' (12/09/2026): 40 of 100 rows had problems and were left out."
    )


def test_where_the_records_came_from(api, db, business):
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    add_sale(db, business, date(2026, 9, 11), cost="1.00")
    with scoped(db, business):
        db.execute(text("UPDATE sales SET source = 'csv' WHERE sold_on = '2026-09-11'"))
    rows = {(o["source"], o["dataset"]): o["records"] for o in report(api, business)["origins"]}
    assert rows == {("csv", "sales"): 1, ("manual", "sales"): 1}


def test_the_hash_join_setting_does_not_leak(api, db, business):
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    report(api, business)
    assert db.execute(text("SHOW enable_nestloop")).scalar() == "on"


# --- who may see it ------------------------------------------------


def test_viewers_can_read_the_score_but_not_refresh_it(api, db, business):
    add_sale(db, business, date(2026, 9, 10), cost="1.00")
    assert get(api, business, who="viewer", as_of=AS_OF).status_code == 200
    assert get(api, business, "/issues", who="viewer").status_code == 200
    assert refresh(api, business, who="viewer").status_code == 403


def test_logging_in_is_required(api, business):
    base = f"{ORGS}/{business[0]}/data-quality"
    assert api.get(base).status_code == 401
    assert api.post(base + "/refresh").status_code == 401
    assert api.get(base + "/issues").status_code == 401


def test_another_business_cannot_read_this_one(api, business):
    for path in ("", "/issues"):
        assert get(api, business, path, who="other", org=0).status_code == 404
    assert refresh(api, business, who="other", org=0).status_code == 404


# --- saving the problems ------------------------------------------------


def seed_problems(db, business):
    add_sale(db, business, date(2026, 6, 10), cost="1.00")
    add_sale(db, business, date(2026, 9, 10), line=False)  # a gap, missing cost


def test_refresh_saves_what_it_found(api, db, business):
    seed_problems(db, business)
    res = refresh(api, business, as_of=AS_OF)
    body = res.json()
    assert res.status_code == 200 and body["new_issues"] == len(body["issues"]) > 0
    assert body["resolved_issues"] == 0
    stored = get(api, business, "/issues").json()
    assert {i["issue_type"] for i in stored} == {i["issue_type"] for i in body["issues"]}
    first = stored[0]
    assert first["resolved_at"] is None and first["message"] and "fix" in first["details"]
    assert set(first) >= {
        "id",
        "dataset",
        "severity",
        "affected_count",
        "period_start",
        "created_at",
    }


def test_refreshing_twice_changes_nothing_the_second_time(api, db, business):
    seed_problems(db, business)
    first = refresh(api, business, as_of=AS_OF).json()
    second = refresh(api, business, as_of=AS_OF).json()
    assert (second["new_issues"], second["resolved_issues"]) == (0, 0)
    assert len(get(api, business, "/issues", status="all").json()) == first["new_issues"]


def test_fixed_problems_are_marked_resolved(api, db, business):
    seed_problems(db, business)
    refresh(api, business, as_of=AS_OF)
    for month in (7, 8):
        add_sale(db, business, date(2026, month, 10), cost="1.00")  # fills the gap
    after = refresh(api, business, as_of=AS_OF).json()
    assert after["resolved_issues"] >= 1
    resolved = get(api, business, "/issues", status="resolved").json()
    assert any(i["issue_type"] == "missing_period" for i in resolved)
    assert all(i["resolved_at"] for i in resolved)
    open_now = get(api, business, "/issues").json()
    assert all(i["resolved_at"] is None for i in open_now)
    assert not any(i["issue_type"] == "missing_period" for i in open_now)


def test_a_continuing_problem_is_updated_not_duplicated(api, db, business):
    add_sale(db, business, date(2026, 9, 10), line=False)
    refresh(api, business, as_of=AS_OF)
    before = next(
        i for i in get(api, business, "/issues").json() if i["issue_type"] == "no_line_detail"
    )
    add_sale(db, business, date(2026, 9, 11), line=False)
    again = refresh(api, business, as_of=AS_OF).json()
    assert again["new_issues"] == 0
    rows = [
        i
        for i in get(api, business, "/issues", status="all").json()
        if i["issue_type"] == "no_line_detail"
    ]
    assert len(rows) == 1 and rows[0]["id"] == before["id"]
    assert rows[0]["affected_count"] == 2 and rows[0]["message"].startswith("2 of your 2 sales")


def test_a_problem_that_returns_is_a_new_issue(api, db, business):
    add_sale(db, business, date(2026, 9, 10), line=False)
    refresh(api, business, as_of=AS_OF)
    with scoped(db, business):
        db.execute(text("DELETE FROM sales"))
    refresh(api, business, as_of=AS_OF)
    add_sale(db, business, date(2026, 9, 12), line=False)
    refresh(api, business, as_of=AS_OF)
    rows = [
        i
        for i in get(api, business, "/issues", status="all").json()
        if i["issue_type"] == "no_line_detail"
    ]
    assert sorted(bool(i["resolved_at"]) for i in rows) == [False, True]


def test_stored_issues_can_be_filtered_and_paged(api, db, business):
    seed_problems(db, business)
    refresh(api, business, as_of=AS_OF)
    everything = get(api, business, "/issues", status="all").json()
    assert len(everything) >= 3
    severities = {i["severity"] for i in everything}
    for severity in severities:
        got = get(api, business, "/issues", severity=severity).json()
        assert got and {i["severity"] for i in got} == {severity}
    assert len(get(api, business, "/issues", limit=1).json()) == 1
    assert len(get(api, business, "/issues", limit=1, offset=1).json()) == 1
    assert get(api, business, "/issues", offset=500).json() == []
    for bad in (
        {"status": "done"},
        {"severity": "urgent"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ):
        assert get(api, business, "/issues", **bad).status_code == 422


def test_each_business_has_its_own_stored_issues(api, db, business):
    add_sale(db, business, date(2026, 9, 10), line=False)
    refresh(api, business, as_of=AS_OF)
    assert get(api, business, "/issues", who="other", org=1).json() == []


def test_refreshing_is_audited_without_detail_about_the_data(api, db, business):
    seed_problems(db, business)
    refresh(api, business, as_of=AS_OF)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "quality.refreshed")).one()
    assert entry.target_type == "data_quality"
    assert set(entry.details) == {"score", "new_issues", "resolved_issues"}


def test_the_stored_report_matches_the_live_one(api, db, business):
    seed_problems(db, business)
    live = report(api, business)
    saved = refresh(api, business, as_of=AS_OF).json()
    for key in ("score", "band", "headline", "datasets", "months", "issues"):
        assert saved[key] == live[key]


def test_a_stale_data_warning_stays_one_issue_as_the_days_pass(api, db, business):
    add_sale(db, business, date(2026, 9, 1), cost="1.00")
    refresh(api, business, as_of="2026-10-02")
    [first] = [i for i in get(api, business, "/issues").json() if i["issue_type"] == "stale_data"]
    later = refresh(api, business, as_of="2026-10-20").json()
    assert later["new_issues"] == 0 and later["resolved_issues"] == 0
    [again] = [i for i in get(api, business, "/issues").json() if i["issue_type"] == "stale_data"]
    assert again["id"] == first["id"] and again["period_end"] == "2026-10-20"
    assert again["affected_count"] == 49 and again["severity"] == "critical"
