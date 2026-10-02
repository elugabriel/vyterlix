"""Phase 4 milestone: a whole (fake) UK business year goes through the real pipeline, and exactly
the totals the generator worked out come out the other end."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.tenant import tenant_scope
from app.demo import milestone
from app.models.business import BusinessListItem
from app.models.data import Customer, Expense, Product, Sale, SaleLine, StockMovement
from app.models.identity import User
from app.services import jobs, kpi

ORGS = "/api/v1/organizations"
D = Decimal


def pence(value) -> str:
    """A database sum as a plain 2-decimal string, like the expected totals."""
    return str(D(value).quantize(D("0.01")))


# --- the generator on its own ---------------------------------------------------------------------


def test_the_same_seed_gives_the_same_year_and_another_seed_a_different_one():
    one, again, other = milestone.build(7), milestone.build(7), milestone.build(8)
    assert one.files == again.files and one.expected == again.expected
    assert one.files["milestone-sales.csv"] != other.files["milestone-sales.csv"]


def test_the_data_is_obviously_fake_and_uk():
    dataset = milestone.build()
    for name, content in dataset.files.items():
        text = content.decode("utf-8-sig")
        assert "@" not in text.replace("@example.com", ""), name  # only example.com emails
    sales = dataset.files["milestone-sales.csv"].decode("utf-8-sig").splitlines()
    assert sales[0].startswith("Date,Receipt No") and sales[1].split(",")[0][2] == "/"  # dd/mm/yyyy
    assert "£" in sales[1]


def test_it_is_a_believable_small_business():
    expected = milestone.build().expected
    net_sales = D(expected["sales"]["net"])
    costs = D(expected["expenses"]["net"])
    assert D("80000") < net_sales < D("130000")
    assert D("0") < net_sales - costs < net_sales * D("0.25")  # a thin profit, not a fantasy
    months = expected["sales"]["months"]
    assert len(months) == 12
    december = D(months["2025-12"]["net"])
    assert december > D(months["2026-01"]["net"]) * D("1.3")  # the Christmas peak and January dip
    assert min(expected["stock"]["closing"].values()) >= 0


# --- through the real pipeline ------------------------------------------------------------------


@pytest.fixture
def business(api, db, signup):
    auth = signup("owner@fakeham.co.uk")
    org_id = api.post(ORGS, json={"name": milestone.BUSINESS_NAME}, headers=auth).json()["id"]
    return org_id, auth


def run_job(api, db, storage, org_id, auth, import_id, action):
    res = api.post(
        f"{ORGS}/{org_id}/imports/{import_id}/jobs", json={"action": action}, headers=auth
    )
    assert res.status_code == 202, res.text
    wanted = res.json()["id"]
    while True:  # an import also queues a KPI recalculation, which may be next in line
        job = jobs.work_once(db, "milestone-worker", storage=storage)
        assert job is not None, "the queue ran dry before the job ran"
        if str(job.id) == wanted:
            break
    assert job.status == "succeeded", (action, job.error_message)
    return job


def bring_in(api, db, storage, org_id, auth, dataset):
    """Upload, match columns, check and import every file, the way a person would."""
    results = {}
    for filename, kind, options in dataset.plan:
        res = api.post(
            f"{ORGS}/{org_id}/imports",
            files={"file": (filename, dataset.files[filename])},
            data={"dataset": kind},
            headers=auth,
        )
        assert res.status_code == 201, res.text
        import_id = res.json()["id"]
        base = f"{ORGS}/{org_id}/imports/{import_id}"
        suggested = api.get(base + "/mapping", headers=auth).json()["suggested_mapping"]
        res = api.put(
            base + "/mapping", json={"mapping": suggested, "options": options}, headers=auth
        )
        assert res.status_code == 200, (filename, res.text)
        assert res.json()["ready"] is True, filename

        checked = run_job(api, db, storage, org_id, auth, import_id, "validate").result
        assert checked["invalid"] == 0 and checked["duplicate"] == 0, (
            filename,
            checked["problems"],
        )
        assert checked["valid"] == dataset.expected["counts"][kind], filename
        results[kind] = run_job(api, db, storage, org_id, auth, import_id, "import").result
    return results


def test_a_whole_year_arrives_with_exactly_the_expected_totals(api, db, storage, business):
    org_id, auth = business
    dataset = milestone.build()
    expected = dataset.expected

    results = bring_in(api, db, storage, org_id, auth, dataset)
    for kind, count in expected["counts"].items():
        assert results[kind]["data_import"]["imported_count"] == count, kind

    with tenant_scope(db, uuid.UUID(org_id)):
        # counts of everything
        assert (
            db.scalar(select(func.count()).select_from(Customer)) == expected["counts"]["customers"]
        )
        assert (
            db.scalar(select(func.count()).select_from(Product)) == expected["counts"]["products"]
        )
        assert db.scalar(select(func.count()).select_from(Sale)) == expected["counts"]["sales"]
        assert db.scalar(select(func.count()).select_from(SaleLine)) == expected["counts"]["sales"]
        assert (
            db.scalar(select(func.count()).select_from(Expense)) == expected["counts"]["expenses"]
        )
        assert (
            db.scalar(select(func.count()).select_from(StockMovement))
            == expected["counts"]["stock_movements"]
        )

        # sales: refunds are stored as negative amounts on 'refund' rows, so a plain sum is right
        net, vat, gross, refunds = db.execute(
            select(
                func.sum(Sale.net_amount),
                func.sum(Sale.vat_amount),
                func.sum(Sale.gross_amount),
                func.count().filter(Sale.kind == "refund"),
            )
        ).one()
        assert (pence(net), pence(vat), pence(gross)) == (
            expected["sales"]["net"], expected["sales"]["vat"], expected["sales"]["gross"]
        )  # fmt: skip
        assert refunds == expected["sales"]["refund_rows"]

        # ...month by month
        month = func.to_char(Sale.sold_on, "YYYY-MM")
        by_month = {
            m: (n, pence(total))
            for m, n, total in db.execute(
                select(month, func.count(), func.sum(Sale.net_amount)).group_by(month)
            )
        }
        assert by_month == {
            m: (v["rows"], v["net"]) for m, v in expected["sales"]["months"].items()
        }

        # cost of goods and who bought
        cost = db.scalar(select(func.sum(SaleLine.cost_amount)))
        assert pence(cost) == expected["sales"]["cost_of_goods"]
        buyers = db.scalar(select(func.count(func.distinct(Sale.customer_id))))
        assert buyers == expected["sales"]["customers_who_bought"]

        # expenses, in total and month by month
        e_net, e_vat = db.execute(
            select(func.sum(Expense.net_amount), func.sum(Expense.vat_amount))
        ).one()
        assert (pence(e_net), pence(e_vat)) == (
            expected["expenses"]["net"],
            expected["expenses"]["vat"],
        )
        e_month = func.to_char(Expense.spent_on, "YYYY-MM")
        e_by_month = {
            m: (n, pence(total))
            for m, n, total in db.execute(
                select(e_month, func.count(), func.sum(Expense.net_amount)).group_by(e_month)
            )
        }
        assert e_by_month == {
            m: (v["rows"], v["net"]) for m, v in expected["expenses"]["months"].items()
        }
        assert (
            db.scalar(
                select(func.count()).select_from(Expense).where(Expense.cost_category_id.is_(None))
            )
            == 0
        )  # every expense was categorised

        # stock on hand per product
        closing = {
            sku: int(total)
            for sku, total in db.execute(
                select(Product.sku, func.sum(StockMovement.quantity))
                .join(Product, Product.id == StockMovement.product_id)
                .group_by(Product.sku)
            )
        }
        assert closing == expected["stock"]["closing"]

    # the KPI engine, run on the whole year, agrees with the generator's own totals
    with tenant_scope(db, uuid.UUID(org_id)):
        stock_category = db.scalars(
            select(BusinessListItem).where(BusinessListItem.name == "Stock")
        ).one()
        stock_category.is_cost_of_sales = True  # what the owner says in onboarding
        db.flush()
        owner = db.scalars(select(User).where(User.email == "owner@fakeham.co.uk")).one()
        run = kpi.calculate(db, jobs.JobTenant(uuid.UUID(org_id), owner), today=date(2026, 10, 2))
    assert run.status == "succeeded" and run.period_from == date(2025, 10, 1)
    revenue = api.get(f"{ORGS}/{org_id}/kpis/revenue?limit=24", headers=auth).json()["values"]
    assert revenue[-1]["is_complete"] is False and revenue[-1]["value"] == "0.00"  # October 2026
    finished = [v for v in revenue if v["is_complete"]]
    assert {v["period_start"][:7]: v["value"] for v in finished} == {
        month: pence(figures["net"]) for month, figures in expected["sales"]["months"].items()
    }
    opex = api.get(f"{ORGS}/{org_id}/kpis/operating_expenses?limit=24", headers=auth).json()[
        "values"
    ]
    bought = api.get(f"{ORGS}/{org_id}/kpis/stock_purchases?limit=24", headers=auth).json()[
        "values"
    ]
    spent = {v["period_start"][:7]: D(v["value"]) for v in opex if v["is_complete"]}
    for v in bought:
        if v["is_complete"]:
            spent[v["period_start"][:7]] += D(v["value"])
    assert {m: pence(t) for m, t in spent.items()} == {
        m: pence(f["net"]) for m, f in expected["expenses"]["months"].items()
    }  # running costs + stock bought = every expense, none lost and none counted twice
    year_profit = sum(
        D(v["value"])
        for v in api.get(f"{ORGS}/{org_id}/kpis/net_profit?limit=24", headers=auth).json()["values"]
    )
    assert D("0") < year_profit < D(expected["sales"]["net"]) * D("0.25")  # a thin, real profit

    # customers: everyone who bought is a *new* customer in exactly one month
    new_customers = api.get(f"{ORGS}/{org_id}/kpis/new_customers?limit=24", headers=auth).json()
    assert (
        sum(int(v["value"]) for v in new_customers["values"] if v["status"] == "ok")
        == (expected["sales"]["customers_who_bought"])
    )
    # stock: what the KPI says is on hand at the end equals what the generator says
    units = api.get(f"{ORGS}/{org_id}/kpis/stock_units?limit=24", headers=auth).json()["values"]
    september = next(v for v in units if v["period_start"] == "2026-09-01")
    assert int(september["value"]) == sum(expected["stock"]["closing"].values())
    # breakdowns add back up to total sales, however they are sliced
    year = {"from": "2025-10-01", "to": "2026-09-30", "limit": 50}
    for dimension in ("channel", "product"):
        res = api.get(f"{ORGS}/{org_id}/kpis/breakdown/{dimension}", params=year, headers=auth)
        assert res.json()["total_revenue"] == pence(expected["sales"]["net"]), dimension
        assert {r["label"] for r in res.json()["rows"]} >= (
            {"Shop", "Website", "Market stall"} if dimension == "channel" else {"Demo Croissant"}
        )

    # the data-quality screen agrees this is a healthy, complete year
    quality = api.get(f"{ORGS}/{org_id}/data-quality", headers=auth).json()
    assert quality["band"] == "good", quality["headline"]
    assert quality["score"] >= 90
    assert quality["issues"] == [] or all(i["severity"] == "info" for i in quality["issues"])
    assert quality["missing_datasets"] == []


def test_importing_the_year_a_second_time_adds_nothing(api, db, storage, business):
    org_id, auth = business
    dataset = milestone.build()
    bring_in(api, db, storage, org_id, auth, dataset)

    # Same sales file again: every receipt number is already there, so nothing is imported twice.
    filename, kind, options = next(p for p in dataset.plan if p[1] == "sales")
    res = api.post(
        f"{ORGS}/{org_id}/imports",
        files={"file": (filename, dataset.files[filename])},
        data={"dataset": kind},
        headers=auth,
    )
    import_id = res.json()["id"]
    base = f"{ORGS}/{org_id}/imports/{import_id}"
    suggested = api.get(base + "/mapping", headers=auth).json()["suggested_mapping"]
    api.put(base + "/mapping", json={"mapping": suggested, "options": options}, headers=auth)
    checked = run_job(api, db, storage, org_id, auth, import_id, "validate").result
    assert checked["valid"] == 0
    assert checked["duplicate"] == dataset.expected["counts"]["sales"]
    assert checked["can_import"] is False


# --- the demo command ------------------------------------------------------------------


def counts(db, org_id) -> dict[str, int]:
    with tenant_scope(db, uuid.UUID(org_id)):
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Customer, Product, Sale, SaleLine, Expense, StockMovement)
        }


def test_the_demo_command_loads_the_year_refuses_twice_and_clears_it(db, storage, business):
    from app.cli.demo import DemoError, clear_year, load_year

    org_id = uuid.UUID(business[0])
    said: list[str] = []
    load_year(db, org_id, storage, said.append)
    assert counts(db, business[0])["sales"] == milestone.build().expected["counts"]["sales"]
    assert any("milestone-sales.csv" in line and "rows imported" in line for line in said)

    with pytest.raises(DemoError, match="already loaded"):
        load_year(db, org_id, storage, said.append)

    clear_year(db, org_id, said.append)
    assert set(counts(db, business[0]).values()) == {0}
    clear_year(db, org_id, said.append)  # clearing again is harmless
    assert said[-1].startswith("Nothing to clear")


def test_the_demo_command_will_not_touch_a_business_that_is_not_marked_as_demo(
    api, db, storage, signup
):
    from app.cli.demo import DemoError, clear_year, load_year

    auth = signup("owner@realshop.co.uk")
    org_id = api.post(ORGS, json={"name": "Real Shop Ltd"}, headers=auth).json()["id"]
    for command in (load_year, clear_year):
        with pytest.raises(DemoError, match="not a demo business"):
            command(db, uuid.UUID(org_id))
    assert set(counts(db, org_id).values()) == {0}


def test_the_demo_command_can_write_the_files_for_uploading_by_hand(tmp_path):
    from app.cli.demo import write_files

    say: list[str] = []
    write_files(tmp_path / "out", say.append)
    written = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert written == sorted(
        [name for name, _, _ in milestone.FILES] + ["milestone-expected-totals.json"]
    )
    assert "customers, products, sales, expenses, stock" in say[-1]
