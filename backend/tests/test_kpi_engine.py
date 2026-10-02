"""The KPI engine: safe formulas, calendar periods, the measures, and KPIs worked out for a
business from real records, checked against hand-calculated numbers."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.permissions import KpiCategory
from app.db.tenant import ACROSS_TENANTS, tenant_scope
from app.kpi import periods
from app.kpi.expression import ExpressionError, compile_expression, evaluate
from app.kpi.measures import MEASURES
from app.models.business import BusinessListItem
from app.models.data import Expense, Sale, SaleLine
from app.models.identity import User
from app.models.jobs import Job
from app.models.kpi import KpiCalculationRun, KpiDefinition, KpiValue
from app.services import jobs, kpi
from app.services.jobs import JobTenant

ORGS = "/api/v1/organizations"
D = Decimal
TODAY = date(2026, 3, 15)  # a Sunday in the middle of March 2026: March is the period in progress


# --- formulas ----------------------------------------------------------------------------


def values(**named):
    """A reader for evaluate(): measures by name, "now" only."""
    return lambda shift, name: named.get(name) if shift == "now" else named.get(f"{shift}:{name}")


def test_formulas_use_ordinary_arithmetic_with_the_usual_precedence():
    ok = compile_expression("(a - b) / a * 100 + 2 * 3")
    assert evaluate(ok, values(a=D("200"), b=D("50"))) == D("81")  # 75 + 6
    assert evaluate(compile_expression("-a + 10"), values(a=D("4"))) == D("6")


def test_a_missing_value_or_a_division_by_zero_means_it_cannot_be_worked_out():
    assert evaluate(compile_expression("a / b"), values(a=D("1"), b=D("0"))) is None
    assert evaluate(compile_expression("a + b"), values(a=D("1"))) is None  # b is missing
    assert (
        evaluate(compile_expression("(a - b) / (c - c)"), values(a=D("1"), b=D("2"), c=D("3")))
        is None
    )


def test_previous_period_and_last_year_are_read_with_their_own_functions():
    compiled = compile_expression("(a - prev(a)) / prev(a) * 100 + yoy(a)")
    assert sorted(compiled.reads) == [("now", "a"), ("prev", "a"), ("yoy", "a")]
    assert compiled.measures == {"a"} and compiled.shifts == {"now", "prev", "yoy"}
    got = evaluate(compiled, values(a=D("150"), **{"prev:a": D("100"), "yoy:a": D("7")}))
    assert got == D("57")


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('x')",
        "a ** 2",
        "a % 2",
        "a // 2",
        "a.real",
        "a if b else c",
        "a < b",
        "[a]",
        "lambda: a",
        "prev(a, b)",
        "prev(a + b)",
        "prev(prev(a))",
        "other(a)",
        "a(b)",
        "'text'",
        "True",
        "a; b",
        "",
        "   ",
        "a +",
        "1 + " * 80 + "1",
    ],
)
def test_anything_but_plain_arithmetic_is_refused(formula):
    with pytest.raises(ExpressionError):
        compile_expression(formula)


def test_a_formula_may_only_use_measures_that_exist():
    with pytest.raises(ExpressionError, match="Unknown measure: nope"):
        compile_expression("revenue - nope", set(MEASURES))
    compile_expression("revenue - cogs", set(MEASURES))


def test_every_seeded_kpi_has_a_valid_formula_over_real_measures(db):
    definitions = db.scalars(select(KpiDefinition)).all()
    assert len(definitions) >= 15
    codes = [d.code for d in definitions]
    assert len(codes) == len(set(codes))
    for definition in definitions:
        compiled = compile_expression(definition.expression, set(MEASURES))
        assert compiled.measures, definition.code
        needed = {MEASURES[m].dataset for m in compiled.measures}
        assert needed <= set(definition.requires) | {"sales"}, definition.code
        assert definition.category in {c.value for c in KpiCategory}
        assert definition.description.endswith("."), definition.code


# --- calendar periods --------------------------------------------------------------------


def test_weeks_start_on_monday_and_quarters_are_calendar_quarters():
    assert periods.start_of(date(2026, 3, 15), "week") == date(2026, 3, 9)  # Sunday -> Monday
    assert periods.start_of(date(2026, 3, 9), "week") == date(2026, 3, 9)
    assert periods.start_of(date(2026, 11, 20), "month") == date(2026, 11, 1)
    assert periods.start_of(date(2026, 5, 31), "quarter") == date(2026, 4, 1)
    assert periods.start_of(date(2026, 12, 31), "year") == date(2026, 1, 1)


@pytest.mark.parametrize(
    ("month", "quarter_start"),
    [
        (1, 1),
        (2, 1),
        (3, 1),
        (4, 4),
        (5, 4),
        (6, 4),
        (7, 7),
        (8, 7),
        (9, 7),
        (10, 10),
        (11, 10),
        (12, 10),
    ],
)
def test_every_month_belongs_to_the_right_calendar_quarter(month, quarter_start):
    assert periods.start_of(date(2026, month, 17), "quarter") == date(2026, quarter_start, 1)


def test_a_sale_on_the_last_day_of_a_period_counts_in_it(api, db, business):
    sale(db, business, date(2026, 1, 31), "10", cost="1")  # the last day of January
    sale(db, business, date(2026, 2, 28), "20", cost="2")  # the last day of the last period
    run(db, business, first=date(2026, 1, 1), last=date(2026, 2, 1))
    months = by_month(history(api, business, "revenue").json())
    assert months["2026-01-01"]["value"] == "10.00" and months["2026-02-01"]["value"] == "20.00"


def test_periods_shift_across_year_ends_and_know_their_last_day():
    assert periods.shift(date(2026, 1, 1), "month", -1) == date(2025, 12, 1)
    assert periods.shift(date(2025, 11, 1), "month", 3) == date(2026, 2, 1)
    assert periods.shift(date(2026, 1, 1), "quarter", -1) == date(2025, 10, 1)
    assert periods.shift(date(2026, 3, 9), "week", -1) == date(2026, 3, 2)
    assert periods.end_of(date(2026, 2, 1), "month") == date(2026, 2, 28)
    assert periods.end_of(date(2024, 2, 1), "month") == date(2024, 2, 29)
    assert periods.end_of(date(2026, 3, 9), "week") == date(2026, 3, 15)
    assert periods.end_of(date(2026, 1, 1), "quarter") == date(2026, 3, 31)


def test_the_same_period_last_year():
    assert periods.same_period_last_year(date(2026, 3, 1), "month") == date(2025, 3, 1)
    assert periods.same_period_last_year(date(2026, 1, 1), "quarter") == date(2025, 1, 1)
    last_year = periods.same_period_last_year(date(2026, 3, 9), "week")
    assert last_year == date(2025, 3, 10) and last_year.weekday() == 0  # still a Monday


def test_a_series_runs_from_the_first_period_to_the_last():
    assert periods.series(date(2025, 11, 1), date(2026, 2, 1), "month") == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)
    ]  # fmt: skip


# --- a small business with hand-worked numbers -------------------------------------------


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def sale(db, business, day, net, cost=None, qty=1, kind="sale", org=0):
    with scoped(db, business, org):
        net = D(net) * (-1 if kind == "refund" else 1)
        record = Sale(
            sold_on=day, kind=kind, net_amount=net, vat_amount=net / 5, gross_amount=net * D("1.2")
        )
        db.add(record)
        db.flush()
        db.add(
            SaleLine(
                sale_id=record.id,
                quantity=D(qty) * (-1 if kind == "refund" else 1),
                net_amount=net,
                vat_amount=net / 5,
                cost_amount=D(cost) if cost is not None else None,
            )
        )
        db.flush()


def category(db, business, name, *, cost_of_sales, org=0):
    with scoped(db, business, org):
        item = BusinessListItem(kind="cost_category", name=name, is_cost_of_sales=cost_of_sales)
        db.add(item)
        db.flush()
        return item.id


def expense(db, business, day, net, category_id=None, kind="expense", org=0):
    with scoped(db, business, org):
        net = D(net) * (-1 if kind == "credit" else 1)
        db.add(
            Expense(
                spent_on=day,
                kind=kind,
                net_amount=net,
                vat_amount=net / 5,
                gross_amount=net * D("1.2"),
                cost_category_id=category_id,
            )
        )
        db.flush()


def owner_tenant(db, business, org=0):
    owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    return JobTenant(organization_id=uuid.UUID(business[org]), user=owner)


@pytest.fixture
def shop(db, business):
    """January: sales 100 (cost 40) and 50 (cost 20), a refund of 10 (cost 4). Running costs
    30 rent + 10 uncategorised - 5 credit, and 20 of stock bought. February: one sale of 210
    (cost 70) and 40 rent. March (in progress): a sale of 100 (cost 50). February last year: 100."""
    rent = category(db, business, "Rent", cost_of_sales=False)
    stock = category(db, business, "Stock", cost_of_sales=True)
    sale(db, business, date(2026, 1, 5), "100", cost="40")
    sale(db, business, date(2026, 1, 20), "50", cost="20", qty=2)
    sale(db, business, date(2026, 1, 21), "10", cost="4", kind="refund")
    expense(db, business, date(2026, 1, 2), "30", rent)
    expense(db, business, date(2026, 1, 3), "20", stock)
    expense(db, business, date(2026, 1, 4), "10")  # no category: counts as a running cost
    expense(db, business, date(2026, 1, 28), "5", rent, kind="credit")
    sale(db, business, date(2026, 2, 10), "210", cost="70")
    expense(db, business, date(2026, 2, 2), "40", rent)
    sale(db, business, date(2026, 3, 5), "100", cost="50")
    sale(db, business, date(2025, 2, 12), "100", cost="30")
    return business


def run(db, business, org=0, **options):
    """The engine as the worker runs it: for one business, with the session scoped to it."""
    options.setdefault("today", TODAY)
    with scoped(db, business, org):
        return kpi.calculate(db, owner_tenant(db, business, org), **options)


def history(api, business, code, who="owner", org=0, **params):
    url = f"{ORGS}/{business[org]}/kpis/{code}"
    return api.get(url, params=params or None, headers=business[2][who])


def by_month(body):
    return {v["period_start"]: v for v in body["values"]}


def test_the_financial_kpis_match_the_hand_worked_numbers(api, db, shop):
    done = run(db, shop, first=date(2026, 1, 1))
    assert done.status == "succeeded" and done.values_written == done.kpi_count * 3

    def jan(code, key="value"):
        return by_month(history(api, shop, code).json())["2026-01-01"][key]

    assert jan("revenue") == "140.00"  # 100 + 50 - 10
    assert jan("gross_sales") == "168.00"  # with VAT at 20%
    assert jan("cost_of_goods_sold") == "56.00"  # 40 + 20 - 4: the refund gives its cost back
    assert jan("gross_profit") == "84.00"
    assert jan("gross_margin_pct") == "60.00"
    assert jan("operating_expenses") == "35.00"  # 30 + 10 - 5 credit; the stock is NOT in here
    assert jan("stock_purchases") == "20.00"
    assert jan("net_profit") == "49.00"
    assert jan("net_margin_pct") == "35.00"


def test_the_sales_kpis_match_the_hand_worked_numbers(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))

    def get(code, month):
        return by_month(history(api, shop, code).json())[month]["value"]

    assert get("sales_count", "2026-01-01") == "2"  # the refund is not a sale
    assert get("average_order_value", "2026-01-01") == "75.00"  # 150 / 2
    assert get("units_sold", "2026-01-01") == "2"  # 1 + 2 - 1
    assert get("refund_rate_pct", "2026-01-01") == "50.00"
    assert get("revenue", "2026-02-01") == "210.00"
    assert get("revenue_growth_pct", "2026-02-01") == "50.00"  # (210 - 140) / 140
    assert get("revenue_growth_pct", "2026-03-01") == "-52.38"  # (100 - 210) / 210
    assert get("revenue_vs_last_year_pct", "2026-02-01") == "110.00"  # vs 100 a year ago


def test_each_value_carries_the_previous_period_and_the_change(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    feb = by_month(history(api, shop, "revenue").json())["2026-02-01"]
    assert (feb["value"], feb["previous_value"], feb["change_pct"]) == ("210.00", "140.00", "50.00")
    mar = by_month(history(api, shop, "gross_profit").json())["2026-03-01"]
    assert (mar["value"], mar["previous_value"], mar["change_pct"]) == ("50.00", "140.00", "-64.29")


def test_a_period_still_running_is_marked_as_not_complete(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    months = by_month(history(api, shop, "revenue").json())
    assert [m["is_complete"] for m in months.values()] == [True, True, False]
    assert months["2026-03-01"]["period_end"] == "2026-03-31"


def test_every_number_can_be_traced_to_the_measures_it_came_from(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    body = history(api, shop, "net_profit").json()
    assert body["formula"] == "revenue - cogs - operating_expenses"
    assert by_month(body)["2026-01-01"]["inputs"] == {
        "revenue": "140.0000", "cogs": "56.0000", "operating_expenses": "35.0000"
    }  # fmt: skip
    growth = by_month(history(api, shop, "revenue_growth_pct").json())["2026-02-01"]
    assert growth["inputs"] == {"revenue": "210.0000", "prev(revenue)": "140.0000"}


def test_a_month_with_no_sales_is_zero_but_its_margin_cannot_be_worked_out(api, db, shop):
    run(db, shop, first=date(2025, 3, 1), last=date(2025, 4, 1))
    body = by_month(history(api, shop, "revenue").json())
    assert body["2025-03-01"]["status"] == "ok" and body["2025-03-01"]["value"] == "0.00"
    margin = by_month(history(api, shop, "gross_margin_pct").json())["2025-03-01"]
    assert margin["status"] == "undefined" and margin["value"] is None


def test_kpis_needing_records_the_business_does_not_have_say_so(api, db, business):
    sale(db, business, date(2026, 1, 5), "100", cost="40")  # sales, but no expenses at all
    run(db, business, first=date(2026, 1, 1))
    profit = by_month(history(api, business, "net_profit").json())["2026-01-01"]
    assert profit["status"] == "no_data" and profit["value"] is None
    costs = by_month(history(api, business, "operating_expenses").json())["2026-01-01"]
    assert costs["status"] == "no_data"
    assert by_month(history(api, business, "gross_profit").json())["2026-01-01"]["value"] == "60.00"


def test_with_no_records_at_all_the_run_succeeds_and_writes_nothing(api, db, business):
    done = run(db, business)
    assert done.status == "succeeded" and done.values_written == 0
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(KpiValue)) == 0


def test_by_default_it_covers_the_first_record_up_to_the_period_in_progress(api, db, shop):
    done = run(db, shop)
    assert (done.period_from, done.period_to) == (date(2025, 2, 1), date(2026, 3, 31))
    months = history(api, shop, "revenue", limit=120).json()["values"]
    assert len(months) == 14 and months[0]["period_start"] == "2025-02-01"


def test_recalculating_replaces_the_earlier_answers_instead_of_adding_to_them(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    sale(db, shop, date(2026, 2, 11), "90", cost="10")  # more data arrives
    run(db, shop, first=date(2026, 1, 1))
    with scoped(db, shop):
        rows = db.scalar(select(func.count()).select_from(KpiValue))
        assert rows == len(kpi.active_definitions(db)) * 3  # not doubled
        runs = db.scalar(select(func.count()).select_from(KpiCalculationRun))
        assert runs == 2
    assert by_month(history(api, shop, "revenue").json())["2026-02-01"]["value"] == "300.00"


def test_data_quality_is_attached_to_each_period(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    months = by_month(history(api, shop, "revenue").json())
    assert all(
        isinstance(m["data_quality"], int) and 0 <= m["data_quality"] <= 100
        for m in months.values()
    )


def test_weeks_are_worked_out_from_monday_to_sunday(api, db, business):
    sale(db, business, date(2026, 3, 9), "10", cost="1")  # Monday
    sale(db, business, date(2026, 3, 15), "20", cost="2")  # Sunday, same week
    sale(db, business, date(2026, 3, 16), "40", cost="4")  # next Monday
    run(db, business, granularity="week", today=date(2026, 3, 18), first=date(2026, 3, 9))
    weeks = by_month(history(api, business, "revenue", granularity="week").json())
    assert (
        weeks["2026-03-09"]["value"] == "30.00"
        and weeks["2026-03-09"]["period_end"] == "2026-03-15"
    )
    assert weeks["2026-03-16"]["value"] == "40.00" and weeks["2026-03-16"]["is_complete"] is False


# --- new KPIs are data, not code ---------------------------------------------------------


def test_a_new_kpi_that_recombines_existing_measures_is_just_a_new_row(api, db, shop):
    db.add(
        KpiDefinition(
            code="cost_per_item", name="Cost per item", description="What each item cost.",
            category="financial", unit="gbp", expression="cogs / units_sold", direction="down_good",
            requires=["sales"], sort_order=999,
        )
    )  # fmt: skip
    db.flush()
    run(db, shop, first=date(2026, 1, 1))
    assert by_month(history(api, shop, "cost_per_item").json())["2026-01-01"]["value"] == "28.00"


def test_an_inactive_kpi_is_not_calculated_or_listed(api, db, shop):
    db.execute(
        KpiDefinition.__table__.update()
        .where(KpiDefinition.code == "units_sold")
        .values(is_active=False)
    )
    run(db, shop, first=date(2026, 1, 1))
    assert history(api, shop, "units_sold").status_code == 404
    listed = api.get(f"{ORGS}/{shop[0]}/kpis", headers=shop[2]["owner"]).json()["kpis"]
    assert "units_sold" not in [k["code"] for k in listed]


def test_one_broken_formula_does_not_stop_the_other_kpis(api, db, shop):
    db.execute(
        KpiDefinition.__table__.update()
        .where(KpiDefinition.code == "units_sold")
        .values(expression="a ** 2")
    )
    done = run(db, shop, first=date(2026, 1, 1))
    assert done.status == "succeeded"
    assert by_month(history(api, shop, "revenue").json())["2026-01-01"]["value"] == "140.00"
    assert by_month(history(api, shop, "units_sold").json()) == {}  # skipped, so nothing saved


def test_a_kpi_limited_to_another_industry_is_not_shown(api, db, shop):
    industry = db.scalars(select(KpiDefinition.industry_code).limit(1)).first()  # None: all
    assert industry is None
    from app.models.business import Industry

    code = db.scalars(select(Industry.code).limit(1)).one()
    db.add(
        KpiDefinition(
            code="only_for_one_industry", name="Only for one", description="Specific.",
            category="sales", unit="count", expression="sales_count", requires=["sales"],
            industry_code=code, sort_order=1000,
        )
    )  # fmt: skip
    db.flush()
    run(db, shop, first=date(2026, 1, 1))
    assert history(api, shop, "only_for_one_industry").status_code == 404  # no profile industry


# --- reading -----------------------------------------------------------------------------


def test_the_list_gives_the_latest_finished_period_and_the_one_in_progress(api, db, shop):
    run(db, shop, first=date(2026, 1, 1))
    body = api.get(f"{ORGS}/{shop[0]}/kpis", headers=shop[2]["owner"]).json()
    revenue = next(k for k in body["kpis"] if k["code"] == "revenue")
    assert (
        revenue["latest"]["period_start"] == "2026-02-01" and revenue["latest"]["value"] == "210.00"
    )
    assert (
        revenue["current"]["period_start"] == "2026-03-01"
        and revenue["current"]["is_complete"] is False
    )
    assert revenue["unit"] == "gbp" and revenue["direction"] == "up_good" and revenue["description"]
    assert body["last_run"]["status"] == "succeeded" and body["last_run"]["trigger"] == "manual"
    categories = [k["category"] for k in body["kpis"]]
    assert categories == sorted(categories)  # grouped for the dashboard


def test_before_the_first_run_the_list_has_definitions_but_no_values(api, business):
    body = api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["owner"]).json()
    assert body["last_run"] is None and len(body["kpis"]) >= 15
    assert all(k["latest"] is None and k["current"] is None for k in body["kpis"])


def test_history_is_oldest_first_and_can_be_limited(api, db, shop):
    run(db, shop)
    body = history(api, shop, "revenue", limit=3).json()
    assert [v["period_start"] for v in body["values"]] == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_bad_requests_are_refused(api, business):
    assert history(api, business, "no_such_kpi").status_code == 404
    assert history(api, business, "revenue", granularity="decade").status_code == 422
    assert history(api, business, "revenue", limit=0).status_code == 422
    assert history(api, business, "revenue", limit=500).status_code == 422


# --- who can do what, and keeping businesses apart ---------------------------------------


def test_viewers_can_read_kpis_but_not_start_a_calculation(api, business):
    assert api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["viewer"]).status_code == 200
    assert history(api, business, "revenue", who="viewer").status_code == 200
    res = api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["viewer"])
    assert res.status_code == 403


def test_each_business_only_ever_sees_its_own_numbers(api, db, shop):
    sale(db, shop, date(2026, 1, 8), "999", cost="1", org=1)  # the rival's big sale
    run(db, shop, first=date(2026, 1, 1))
    run(db, shop, org=1, first=date(2026, 1, 1))
    # owner_tenant uses owner@acme for both, but the session scope decides whose data is read
    rival = by_month(history(api, shop, "revenue", who="other", org=1).json())
    mine = by_month(history(api, shop, "revenue").json())
    assert mine["2026-01-01"]["value"] == "140.00"
    assert rival["2026-01-01"]["value"] == "999.00"
    assert api.get(f"{ORGS}/{shop[1]}/kpis", headers=shop[2]["owner"]).status_code == 404


def test_a_calculation_runs_in_the_background_and_is_recorded(api, db, shop, storage):
    res = api.post(f"{ORGS}/{shop[0]}/kpis/calculate", headers=shop[2]["owner"])
    assert res.status_code == 202 and res.json()["kind"] == "kpi.calculate"
    again = api.post(f"{ORGS}/{shop[0]}/kpis/calculate", headers=shop[2]["owner"])
    assert again.status_code == 409  # one at a time per business
    ran = jobs.work_once(db, "kpi-worker", storage=storage)
    assert ran.status == "succeeded" and ran.result["status"] == "succeeded"
    assert ran.result["trigger"] == "manual" and ran.result["values_written"] > 0
    body = api.get(f"{ORGS}/{shop[0]}/kpis", headers=shop[2]["owner"]).json()
    assert body["last_run"]["id"] == ran.result["id"]
    assert any(k["latest"] is not None for k in body["kpis"])


def test_a_failed_calculation_is_recorded_and_shows_a_plain_message(db, shop, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(kpi, "_calculate", boom)
    with pytest.raises(RuntimeError):
        run(db, shop, first=date(2026, 1, 1))
    with scoped(db, shop):
        failed = db.scalars(select(KpiCalculationRun)).one()
    assert failed.status == "failed" and failed.finished_at is not None
    assert "hiccup" not in failed.error_message and "try again" in failed.error_message


# --- imports refresh the KPIs ------------------------------------------------------------


def upload_sales(api, business, rows):
    csv = ("Date,Order,Total,VAT\n" + "\n".join(rows) + "\n").encode()
    headers = business[2]["owner"]
    res = api.post(
        f"{ORGS}/{business[0]}/imports", files={"file": ("s.csv", csv)}, data={"dataset": "sales"},
        headers=headers,
    )  # fmt: skip
    import_id = res.json()["id"]
    base = f"{ORGS}/{business[0]}/imports/{import_id}"
    mapping = {"sold_on": "Date", "amount": "Total", "vat_amount": "VAT", "reference": "Order"}
    api.put(
        base + "/mapping",
        json={"mapping": mapping, "options": {"vat_inclusive": True}},
        headers=headers,
    )
    return base, headers


def test_importing_queues_a_kpi_recalculation_and_it_sees_the_new_data(api, db, business, storage):
    base, headers = upload_sales(
        api, business, ["28/09/2026,1,12.00,2.00", "29/09/2026,2,24.00,4.00"]
    )
    for action in ("validate", "import"):
        assert api.post(base + "/jobs", json={"action": action}, headers=headers).status_code == 202
        jobs.work_once(db, "w", storage=storage)
    with scoped(db, business):
        queued = db.scalars(select(Job).where(Job.kind == "kpi.calculate")).all()
    assert (
        len(queued) == 1
        and queued[0].status == "queued"
        and queued[0].payload["trigger"] == "import"
    )

    ran = jobs.work_once(db, "w", storage=storage)
    assert (
        ran.kind == "kpi.calculate"
        and ran.status == "succeeded"
        and ran.result["trigger"] == "import"
    )
    revenue = by_month(history(api, business, "revenue").json())["2026-09-01"]
    assert revenue["value"] == "30.00"  # 10 + 20 net of VAT


def test_undoing_an_import_queues_a_recalculation_too(api, db, business, storage):
    base, headers = upload_sales(api, business, ["28/09/2026,1,12.00,2.00"])
    for action in ("validate", "import"):
        api.post(base + "/jobs", json={"action": action}, headers=headers)
        jobs.work_once(db, "w", storage=storage)
    jobs.work_once(db, "w", storage=storage)  # the recalculation after the import
    api.post(base + "/jobs", json={"action": "undo"}, headers=headers)
    jobs.work_once(db, "w", storage=storage)
    ran = jobs.work_once(db, "w", storage=storage)
    assert ran.kind == "kpi.calculate" and ran.status == "succeeded"
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_the_kpi_tables_hold_only_ok_values_and_belong_to_their_business(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.execute(
            KpiDefinition.__table__.insert().values(
                id=uuid.uuid4(), code="Bad Code", name="x", description="x", category="sales",
                unit="count", expression="sales_count",
            )
        )  # fmt: skip
        db.flush()
    assert KpiValue.__table__.c.organization_id.foreign_keys  # tenant-scoped like all business data
    assert ACROSS_TENANTS
