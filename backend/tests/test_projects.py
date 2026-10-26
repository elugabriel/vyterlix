# ruff: noqa: E501, F811
"""Projects: what each piece of work has earned and cost (project profitability, client feedback 6)."""

import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models.data import Expense, Project, Sale
from tests.test_import_run import checked, count, undo
from tests.test_import_run import run as run_import
from tests.test_import_run import scoped as scoped_import
from tests.test_manual_records import call, create

ORGS = "/api/v1/organizations"
SALE = {"sold_on": "2026-09-10", "amount": "1200.00", "amount_includes_vat": True, "vat_rate": "20"}
COST = {"spent_on": "2026-09-11", "amount": "600.00", "amount_includes_vat": True, "vat_rate": "20"}


# --- the projects themselves ---------------------------------------------------------------------------


def test_a_project_is_typed_in_and_read_back(api, business):
    made = create(api, business, "projects", {"name": "Kitchen for Acme", "budget": "5000"})
    assert made["status"] == "active" and made["budget"] == "5000.00" and made["source"] == "manual"
    assert call(api, business, "GET", f"projects/{made['id']}").json()["name"] == "Kitchen for Acme"


def test_a_project_can_belong_to_a_client_of_the_business_only(api, business):
    mine = create(api, business, "customers", {"name": "Acme"})
    assert (
        create(api, business, "projects", {"name": "A", "customer_id": mine["id"]})["customer_id"]
        == mine["id"]
    )
    theirs = create(api, business, "customers", {"name": "Other"}, org=1, who="other")
    assert (
        call(
            api, business, "POST", "projects", {"name": "A", "customer_id": theirs["id"]}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": " "},
        {"name": "A", "status": "paused"},
        {"name": "A", "budget": "-1"},
        {"name": "A", "extra": 1},
    ],
)
def test_bad_projects_are_refused(api, business, body):
    assert call(api, business, "POST", "projects", body).status_code == 422


def test_a_project_can_be_finished_and_listed(api, business):
    a = create(api, business, "projects", {"name": "Alpha"})
    create(api, business, "projects", {"name": "Beta"})
    call(api, business, "PATCH", f"projects/{a['id']}", {"status": "finished"})
    assert [p["name"] for p in call(api, business, "GET", "projects").json()["items"]] == [
        "Alpha",
        "Beta",
    ]
    assert [
        p["name"] for p in call(api, business, "GET", "projects", status="finished").json()["items"]
    ] == ["Alpha"]
    assert call(api, business, "GET", "projects", q="bet").json()["total"] == 1


def test_a_project_in_use_cannot_be_deleted_but_an_unused_one_can(api, business):
    used = create(api, business, "projects", {"name": "Used"})
    create(api, business, "sales", {**SALE, "project_id": used["id"]})
    assert call(api, business, "DELETE", f"projects/{used['id']}").status_code == 409
    free = create(api, business, "projects", {"name": "Free"})
    assert call(api, business, "DELETE", f"projects/{free['id']}").status_code == 204


def test_only_those_who_manage_data_use_them_and_each_business_has_its_own(api, business):
    made = create(api, business, "projects", {"name": "A"})
    assert call(api, business, "GET", "projects", who="viewer").status_code == 403
    assert call(api, business, "GET", "projects", org=1, who="other").json()["total"] == 0
    assert (
        call(api, business, "GET", f"projects/{made['id']}", org=1, who="other").status_code == 404
    )


# --- putting sales and costs on a project --------------------------------------------------------------


def test_a_sale_and_a_cost_can_be_put_on_a_project(api, business):
    project = create(api, business, "projects", {"name": "Kitchen"})
    sale = create(api, business, "sales", {**SALE, "project_id": project["id"]})
    cost = create(api, business, "expenses", {**COST, "project_id": project["id"]})
    assert sale["project_id"] == cost["project_id"] == project["id"]
    moved = call(api, business, "PATCH", f"sales/{sale['id']}", {"project_id": None}).json()
    assert moved["project_id"] is None
    moved = call(api, business, "PATCH", f"expenses/{cost['id']}", {"project_id": None}).json()
    assert moved["project_id"] is None


def test_it_must_be_one_of_your_projects(api, business):
    theirs = create(api, business, "projects", {"name": "Theirs"}, org=1, who="other")
    for path, body in (("sales", SALE), ("expenses", COST)):
        res = call(api, business, "POST", path, {**body, "project_id": theirs["id"]})
        assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_reference"


# --- what each project has earned and cost -------------------------------------------------------------


def money(db, business, model, project, net, kind, **fields):
    with scoped_import(db, business):
        net = D(net)
        db.add(
            model(
                project_id=project,
                kind=kind,
                net_amount=net,
                vat_amount=net / 5,
                gross_amount=net * D("1.2"),
                **fields,
            )
        )
        db.flush()


@pytest.fixture
def jobs(api, db, business):
    """Kitchen: sales 5,000 (after a refund of 200), costs 3,000 (after a credit of 100), budget 4,000.
    Bathroom: sales 1,000, costs 1,800, so it loses 800. Garden: nothing on it yet but still active.
    Old: finished with nothing on it, so not listed. And 500 of costs that are on no project."""
    names = {
        "Kitchen": {"budget": "4000"},
        "Bathroom": {},
        "Garden": {},
        "Old": {"status": "finished"},
    }
    pid = {
        n: uuid.UUID(create(api, business, "projects", {"name": n, **extra})["id"])
        for n, extra in names.items()
    }
    money(db, business, Sale, pid["Kitchen"], 5200, "sale", sold_on=date(2026, 9, 1))
    money(db, business, Sale, pid["Kitchen"], -200, "refund", sold_on=date(2026, 9, 2))
    money(db, business, Expense, pid["Kitchen"], 3100, "expense", spent_on=date(2026, 9, 3))
    money(db, business, Expense, pid["Kitchen"], -100, "credit", spent_on=date(2026, 9, 4))
    money(db, business, Sale, pid["Bathroom"], 1000, "sale", sold_on=date(2026, 9, 5))
    money(db, business, Expense, pid["Bathroom"], 1800, "expense", spent_on=date(2026, 9, 6))
    money(db, business, Expense, None, 500, "expense", spent_on=date(2026, 9, 7))
    return business


def profit(api, business, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/kpis/projects", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def test_each_project_with_what_it_earned_and_cost(api, jobs):
    body = profit(api, jobs)
    assert [r["name"] for r in body["rows"]] == [
        "Bathroom",
        "Garden",
        "Kitchen",
    ]  # losing money first
    bath, garden, kitchen = body["rows"]
    assert (bath["revenue"], bath["costs"], bath["profit"], bath["margin_pct"]) == (
        "1000.00",
        "1800.00",
        "-800.00",
        "-80.0",
    )
    assert (kitchen["revenue"], kitchen["costs"], kitchen["profit"], kitchen["margin_pct"]) == (
        "5000.00",
        "3000.00",
        "2000.00",
        "40.0",
    )
    assert (garden["revenue"], garden["costs"], garden["margin_pct"]) == ("0.00", "0.00", None)
    assert "Old" not in [r["name"] for r in body["rows"]]
    assert (
        body["total_revenue"] == "6000.00"
        and body["total_costs"] == "4800.00"
        and body["unallocated_costs"] == "500.00"
    )


def test_the_budget_is_what_the_costs_are_measured_against(api, jobs):
    kitchen = next(r for r in profit(api, jobs)["rows"] if r["name"] == "Kitchen")
    assert kitchen["budget"] == "4000.00" and kitchen["budget_used_pct"] == "75"


def test_it_says_in_plain_words_who_is_losing_money_and_what_is_left_out(api, jobs):
    notes = profit(api, jobs)["notes"]
    assert (
        notes[0] == "Bathroom has cost £1,800.00 and earned £1,000.00: it is losing £800.00 so far."
    )
    assert any("£500.00 of your costs are not on any project" in n for n in notes)


def test_a_project_close_to_its_budget_is_warned_about(api, db, business):
    project = uuid.UUID(
        create(api, business, "projects", {"name": "Tight", "budget": "1000"})["id"]
    )
    money(db, business, Sale, project, 3000, "sale", sold_on=date(2026, 9, 1))
    money(db, business, Expense, project, 950, "expense", spent_on=date(2026, 9, 2))
    assert "Tight has used 95% of its budget." in profit(api, business)["notes"]


def test_no_projects_means_an_empty_answer(api, business):
    assert profit(api, business) == {
        "total_revenue": "0.00",
        "total_costs": "0.00",
        "unallocated_costs": "0.00",
        "rows": [],
        "notes": [],
    }


def test_a_viewer_can_see_it_and_another_business_sees_none_of_it(api, jobs):
    assert len(profit(api, jobs, "viewer")["rows"]) == 3
    assert profit(api, jobs, "other", org=1)["rows"] == []


# --- from a file ----------------------------------------------------------------------------------------


def test_a_file_brings_its_projects_with_it_and_undo_takes_them_away(api, db, business):
    mapping = {
        "sold_on": "Date",
        "amount": "Total",
        "vat_amount": "VAT",
        "project": "Job",
        "reference": "Ref",
    }
    content = b"Date,Ref,Total,VAT,Job\n01/09/2026,A1,1200.00,200.00,Kitchen\n02/09/2026,A2,600.00,100.00,kitchen\n03/09/2026,A3,360.00,60.00,\n"
    with scoped_import(db, business):
        db.add(Project(name="Bathroom"))
        db.flush()
    import_id = checked(api, business, content, mapping, {"vat_inclusive": True}, "sales")
    res = run_import(api, business, import_id)
    assert res.status_code == 200, res.text
    with scoped_import(db, business):
        sales = db.scalars(select(Sale).order_by(Sale.sold_on)).all()
        assert (
            sales[0].project_id == sales[1].project_id
            and sales[0].project_id is not None
            and sales[2].project_id is None
        )
        assert sorted(p.name for p in db.scalars(select(Project))) == ["Bathroom", "Kitchen"]
    assert undo(api, business, import_id).status_code == 200
    with scoped_import(db, business):
        assert [p.name for p in db.scalars(select(Project))] == ["Bathroom"] and count(
            db, Sale
        ) == 0


def test_costs_can_be_put_on_a_project_from_a_file_too(api, db, business):
    mapping = {"spent_on": "Date", "amount": "Total", "vat_amount": "VAT", "project": "Job"}
    content = b"Date,Total,VAT,Job\n01/09/2026,120.00,20.00,Kitchen\n"
    res = run_import(
        api, business, checked(api, business, content, mapping, {"vat_inclusive": True}, "expenses")
    )
    assert res.status_code == 200, res.text
    with scoped_import(db, business):
        [expense] = db.scalars(select(Expense)).all()
        assert db.get(Project, expense.project_id).name == "Kitchen"
