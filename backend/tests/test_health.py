"""Business health: the scoring arithmetic, then the engine on KPI values worked out by hand."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.tenant import tenant_scope
from app.health import scoring
from app.models.health import (
    BusinessHealth,
    BusinessHealthComponent,
    HealthCategoryWeight,
    HealthRule,
)
from app.models.identity import User
from app.models.kpi import KpiCalculationRun, KpiDefinition, KpiValue
from app.services import health
from app.services.jobs import JobTenant

ORGS = "/api/v1/organizations"
D = Decimal
UP = {"direction": "up_good", "bad": D("0"), "ok": D("10"), "good": D("20")}


# --- scoring one metric ------------------------------------------------------------------


def test_a_metric_scores_zero_sixty_and_a_hundred_at_its_three_anchors():
    for value, score in ((0, 0), (10, 60), (20, 100)):
        assert scoring.score_metric(D(value), **UP) == score


def test_between_the_anchors_the_score_runs_in_a_straight_line():
    assert scoring.score_metric(D("5"), **UP) == 30  # halfway from bad to ok
    assert scoring.score_metric(D("15"), **UP) == 80  # halfway from ok to good
    assert scoring.score_metric(D("2.5"), **UP) == 15


def test_beyond_the_ends_the_score_stays_at_zero_and_a_hundred():
    assert scoring.score_metric(D("-50"), **UP) == 0
    assert scoring.score_metric(D("500"), **UP) == 100


def test_when_lower_is_better_the_scale_is_mirrored():
    down = {"direction": "down_good", "bad": D("8"), "ok": D("3"), "good": D("1")}
    assert [scoring.score_metric(D(v), **down) for v in (8, 3, 1)] == [0, 60, 100]
    assert scoring.score_metric(D("5.5"), **down) == 30
    assert scoring.score_metric(D("2"), **down) == 80
    assert scoring.score_metric(D("0"), **down) == 100  # even better than "good"
    assert scoring.score_metric(D("20"), **down) == 0


@pytest.mark.parametrize(
    ("score", "status"),
    [(100, "healthy"), (80, "healthy"), (79, "fair"), (60, "fair"), (59, "needs_attention"),
     (40, "needs_attention"), (39, "at_risk"), (0, "at_risk"), (None, "not_enough_data")],
)  # fmt: skip
def test_the_status_bands(score, status):
    assert scoring.status_for(score) == status


@pytest.mark.parametrize(
    ("score", "before", "trend"),
    [(70, 67, "up"), (70, 68, "flat"), (70, 70, "flat"), (70, 72, "flat"), (70, 73, "down"),
     (70, None, None), (None, 70, None)],
)  # fmt: skip
def test_the_trend_needs_a_three_point_move(score, before, trend):
    assert scoring.trend_for(score, before) == trend


def test_whats_usual_needs_at_least_three_earlier_months():
    assert scoring.baseline_of([D("1"), D("2")]) is None
    assert scoring.baseline_of([D("10"), D("20"), D("30")]) == 20


def test_distance_from_usual_is_a_percentage_and_undefined_for_zero():
    assert scoring.percent_from(D("110"), D("100")) == 10
    assert scoring.percent_from(D("80"), D("100")) == -20
    assert scoring.percent_from(D("-50"), D("-100")) == 50  # a smaller loss is an improvement
    assert scoring.percent_from(D("5"), D("0")) is None


def test_weighted_averages_and_rounding():
    assert scoring.weighted_average([(D("100"), D("3")), (D("40"), D("1"))]) == 85
    assert scoring.weighted_average([]) is None
    assert scoring.weighted_average([(D("50"), D("0"))]) is None
    assert scoring.to_int(D("2.5")) == 3 and scoring.to_int(D("61.49")) == 61


def test_values_are_written_the_way_people_read_them():
    assert scoring.format_value(D("1234.5"), "gbp") == "£1,234.50"
    assert scoring.format_value(D("12.34"), "percent") == "12.3%"
    assert scoring.format_value(D("0.386"), "ratio") == "0.39 times"
    assert scoring.format_value(D("1250"), "count") == "1,250"


def test_a_sentence_says_what_it_was_what_it_is_judged_against_and_what_it_scored():
    against_usual = scoring.describe_metric(
        "Sales", "gbp", "vs_baseline", "up_good", D("900"), D("39.6"),
        bad=D("-20"), good=D("10"), baseline=D("1000"), compared=D("-10"), baseline_months=6,
    )  # fmt: skip
    assert against_usual == (
        "Sales: £900.00, 10% below your usual £1,000.00 (the average of the last 6 months). "
        "That scores 40 out of 100."
    )
    level = scoring.describe_metric(
        "Profit margin", "percent", "value", "up_good", D("12"), D("84"), bad=D("0"), good=D("15")
    )
    assert level == (
        "Profit margin: 12.0%. A good level is 15.0% or more; below 0.0% is a worry. "
        "That scores 84 out of 100."
    )
    refunds = scoring.describe_metric(
        "Refund rate", "percent", "value", "down_good", D("5"), D("30"), bad=D("8"), good=D("1")
    )
    assert "A good level is 1.0% or less; above 8.0% is a worry." in refunds
    steady = scoring.describe_metric(
        "Sales", "gbp", "vs_baseline", "up_good", D("1002"), D("60"),
        bad=D("-20"), good=D("10"), baseline=D("1000"), compared=D("0.2"), baseline_months=6,
    )  # fmt: skip
    assert "in line with your usual £1,000.00" in steady


# --- a business with KPI values worked out by hand ---------------------------------------


def scoped(db, business, org=0):
    return tenant_scope(db, uuid.UUID(business[org]))


def owner_tenant(db, business):
    owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
    return JobTenant(organization_id=uuid.UUID(business[0]), user=owner)


class Kpis:
    """Writes KPI values straight into the table, as the KPI engine would have."""

    def __init__(self, db, business):
        self.db, self.business = db, business
        with scoped(db, business):
            run = KpiCalculationRun(
                granularity="month", period_from=date(2026, 1, 1), period_to=date(2026, 12, 31),
                started_at=datetime.now(UTC), finished_at=datetime.now(UTC), status="succeeded",
            )  # fmt: skip
            db.add(run)
            db.flush()
            self.run_id = run.id

    def put(self, code, month, value, *, status="ok", quality=100, complete=True):
        from calendar import monthrange

        kpi_id = self.db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == code)).one()
        with scoped(self.db, self.business):
            self.db.add(
                KpiValue(
                    kpi_id=kpi_id, run_id=self.run_id, granularity="month", period_start=month,
                    period_end=month.replace(day=monthrange(month.year, month.month)[1]),
                    is_complete=complete, status=status,
                    value=None if value is None else D(str(value)),
                    data_quality=quality, inputs={}, calculated_at=datetime.now(UTC),
                )
            )  # fmt: skip
            self.db.flush()

    def months(self, code, values, start=1):
        for offset, value in enumerate(values):
            self.put(code, date(2026, start + offset, 1), value)


def calculate(db, business):
    with scoped(db, business):
        return health.calculate(db, owner_tenant(db, business))


def get(api, business, path="", who="owner", org=0):
    return api.get(f"{ORGS}/{business[org]}/business-health{path}", headers=business[2][who])


@pytest.fixture
def steady_shop(db, business):
    """January to August 2026, with every month at the 'ok' anchor except August's sales.

    Profit margin 8% (ok), retention 50% (ok), nothing out of stock (perfect). Sales are 1,000 every
    month until August, when they are 1,100: 10% above usual, which is the 'good' anchor.
    """
    kpis = Kpis(db, business)
    kpis.months("net_margin_pct", [8] * 8)
    kpis.months("customer_retention_pct", [50] * 8)
    kpis.months("out_of_stock_pct", [0] * 8)
    kpis.months("revenue", [1000] * 7 + [1100])
    calculate(db, business)
    return business


def component(body, category):
    return next(c for c in body["components"] if c["category"] == category)


def test_the_overall_score_is_a_weighted_average_of_the_areas(api, steady_shop):
    body = get(api, steady_shop).json()
    assert body["period_start"] == "2026-08-01" and body["period_end"] == "2026-08-31"
    scores = {c["category"]: c["score"] for c in body["components"]}
    assert scores == {
        "financial": 60,
        "sales": 100,
        "customer": 60,
        "inventory": 100,
        "marketing": None,
    }  # operational carries no weight yet, so it isn't listed
    # (35 x 60 + 25 x 100 + 20 x 60 + 10 x 100) / 90 = 75.56
    assert body["overall_score"] == 76 and body["status"] == "fair"
    assert body["coverage_pct"] == 90  # marketing (10%) has no data yet


def test_each_area_says_how_many_points_it_added(api, steady_shop):
    body = get(api, steady_shop).json()
    assert component(body, "sales")["contribution"] == pytest.approx(
        27.78, abs=0.01
    )  # 100 x 25 / 90
    assert component(body, "financial")["contribution"] == pytest.approx(23.33, abs=0.01)
    total = sum(c["contribution"] for c in body["components"] if c["contribution"] is not None)
    assert total == pytest.approx(75.56, abs=0.05)  # they add up to the overall score
    assert component(body, "marketing")["contribution"] is None


def test_trend_compares_with_the_month_before(api, steady_shop):
    body = get(api, steady_shop).json()
    # July: sales at their usual level (60), so overall = (35x60 + 25x60 + 20x60 + 10x100) / 90 = 64
    assert body["previous_score"] == 64 and body["trend"] == "up"
    assert component(body, "sales")["previous_score"] == 60
    assert component(body, "sales")["trend"] == "up"
    assert component(body, "financial")["trend"] == "flat"
    assert component(body, "inventory")["trend"] == "flat"


def test_every_score_comes_with_the_evidence_behind_it(api, steady_shop):
    sales = component(get(api, steady_shop).json(), "sales")
    [metric] = sales["metrics"]
    assert (metric["kpi_code"], metric["basis"], metric["score"]) == (
        "revenue",
        "vs_baseline",
        100.0,
    )
    assert (metric["value"], metric["baseline"], metric["baseline_months"]) == (
        "1100.00",
        "1000.00",
        6,
    )
    assert metric["compared_pct"] == "10.0"
    assert metric["text"] == (
        "Sales: £1,100.00, 10% above your usual £1,000.00 (the average of the last 6 months). "
        "That scores 100 out of 100."
    )
    assert sales["explanation"] == "Sales scores 100 out of 100 (healthy)."
    overall = get(api, steady_shop).json()["explanation"]
    assert overall.startswith("Your business health is 76 out of 100 (fair).")
    assert "Strongest area: Sales (100)." in overall and "Weakest: Money (60)." in overall
    assert "Not yet counted: Marketing" in overall and "covers 90%" in overall


def test_an_area_without_enough_history_is_left_out_and_says_why(api, steady_shop):
    january = get(api, steady_shop, "/2026-01-01").json()
    sales = component(january, "sales")
    assert sales["score"] is None and sales["status"] == "not_enough_data"
    assert "there isn't enough history yet to know what is usual for Sales" in sales["explanation"]
    assert january["coverage_pct"] == 65  # financial 35 + customer 20 + inventory 10
    assert january["overall_score"] == 66  # (35 x 60 + 20 x 60 + 10 x 100) / 65 = 66.2
    april = get(api, steady_shop, "/2026-04-01").json()  # three earlier months: enough
    assert component(april, "sales")["score"] == 60 and april["coverage_pct"] == 90


def test_a_trend_is_only_given_against_the_month_straight_before(api, db, business):
    kpis = Kpis(db, business)
    for month in (1, 2, 4):  # March is missing: April has no month straight before it
        kpis.put("net_margin_pct", date(2026, month, 1), 8)
        kpis.put("out_of_stock_pct", date(2026, month, 1), 0)
        kpis.put("customer_retention_pct", date(2026, month, 1), 50)
    calculate(db, business)
    february = get(api, business, "/2026-02-01").json()
    april = get(api, business, "/2026-04-01").json()
    assert february["previous_score"] is not None and february["trend"] == "flat"
    assert april["previous_score"] is None and april["trend"] is None
    assert all(c["trend"] is None for c in april["components"])


def test_the_first_month_has_no_trend(api, steady_shop):
    january = get(api, steady_shop, "/2026-01-01").json()
    assert january["previous_score"] is None and january["trend"] is None
    assert all(c["trend"] is None for c in january["components"])


def test_too_little_of_the_picture_gives_no_overall_score(api, db, business):
    Kpis(db, business).months("out_of_stock_pct", [0] * 3)  # only stock: 10% of the weight
    calculate(db, business)
    body = get(api, business).json()
    assert body["overall_score"] is None and body["status"] == "not_enough_data"
    assert body["coverage_pct"] == 10
    assert body["explanation"].startswith("There isn't enough data yet to give an overall score.")
    assert component(body, "inventory")["score"] == 100  # the area itself can still be shown


def test_a_month_still_in_progress_is_not_judged(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("net_margin_pct", [8] * 3)
    kpis.put("net_margin_pct", date(2026, 4, 1), 1, complete=False)  # part-way through April
    calculate(db, business)
    months = [p["period_start"] for p in get(api, business, "/history").json()["points"]]
    assert months == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_a_kpi_that_could_not_be_worked_out_is_skipped_not_scored_as_zero(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("customer_retention_pct", [50] * 2)
    kpis.put("customer_retention_pct", date(2026, 3, 1), None, status="undefined")
    kpis.months("out_of_stock_pct", [0] * 3)
    calculate(db, business)
    march = get(api, business, "/2026-03-01").json()
    customer = component(march, "customer")
    assert customer["score"] is None
    assert "Customers who came back can't be worked out for this month" in customer["explanation"]


def test_the_lowest_data_quality_among_the_kpis_used_is_reported(api, db, business):
    kpis = Kpis(db, business)
    kpis.put("net_margin_pct", date(2026, 1, 1), 8, quality=95)
    kpis.put("customer_retention_pct", date(2026, 1, 1), 50, quality=60)
    kpis.put("out_of_stock_pct", date(2026, 1, 1), 0, quality=100)
    calculate(db, business)
    assert get(api, business).json()["data_quality"] == 60


# --- the rules are data ------------------------------------------------------------------


def test_recalculating_replaces_the_earlier_scores_instead_of_adding_to_them(api, db, steady_shop):
    calculate(db, steady_shop)
    calculate(db, steady_shop)
    with scoped(db, steady_shop):
        assert db.scalar(select(func.count()).select_from(BusinessHealth)) == 8
        assert (
            db.scalar(select(func.count()).select_from(BusinessHealthComponent)) == 8 * 5
        )  # five areas carry weight


def test_changing_a_rule_changes_the_score_without_changing_code(api, db, steady_shop):
    db.execute(
        HealthRule.__table__.update()
        .where(HealthRule.kpi_code == "net_margin_pct", HealthRule.industry_code.is_(None))
        .values(threshold_bad=D("0"), threshold_ok=D("4"), threshold_good=D("8"))
    )  # now 8% margin is "good", not merely "ok"
    calculate(db, steady_shop)
    assert component(get(api, steady_shop).json(), "financial")["score"] == 100


def test_a_switched_off_rule_no_longer_counts(api, db, steady_shop):
    db.execute(
        HealthRule.__table__.update()
        .where(HealthRule.kpi_code == "out_of_stock_pct")
        .values(is_active=False)
    )
    calculate(db, steady_shop)
    inventory = component(get(api, steady_shop).json(), "inventory")
    assert inventory["score"] is None and inventory["metrics"] == []


def test_category_weights_are_data_too(api, db, steady_shop):
    db.execute(
        HealthCategoryWeight.__table__.update()
        .where(
            HealthCategoryWeight.category == "sales", HealthCategoryWeight.industry_code.is_(None)
        )
        .values(weight=D("0"))  # leave sales out of the score altogether
    )
    calculate(db, steady_shop)
    body = get(api, steady_shop).json()
    assert "sales" not in [c["category"] for c in body["components"]]
    assert body["overall_score"] == 66  # (35 x 60 + 20 x 60 + 10 x 100) / 65
    assert body["coverage_pct"] == 87  # 65 of the 75 that now count: marketing is still empty


def test_a_rule_written_for_an_industry_replaces_the_general_one_for_that_business(
    api, db, steady_shop
):
    # Retail businesses are judged more strictly on margin: 8% is only "bad".
    db.add(HealthRule(
        category="financial", kpi_code="net_margin_pct", basis="value", direction="up_good",
        threshold_bad=D("8"), threshold_ok=D("12"), threshold_good=D("20"), weight=D("3"),
        industry_code="retail",
    ))  # fmt: skip
    db.flush()
    calculate(db, steady_shop)
    general = component(get(api, steady_shop).json(), "financial")
    assert general["score"] == 60  # no profile yet, so the general rule applies
    put = api.put(
        f"{ORGS}/{steady_shop[0]}/profile",
        json={"industry_code": "retail", "financial_year_start": {"month": 4, "day": 1}},
        headers=steady_shop[2]["owner"],
    )
    assert put.status_code == 200, put.text
    calculate(db, steady_shop)
    assert component(get(api, steady_shop).json(), "financial")["score"] == 0  # 8% = the bad anchor


def test_an_industry_can_weigh_the_areas_differently(api, db, steady_shop):
    db.add(HealthCategoryWeight(category="inventory", weight=D("40"), industry_code="retail"))
    db.flush()
    api.put(
        f"{ORGS}/{steady_shop[0]}/profile",
        json={"industry_code": "retail", "financial_year_start": {"month": 4, "day": 1}},
        headers=steady_shop[2]["owner"],
    )
    calculate(db, steady_shop)
    body = get(api, steady_shop).json()
    assert component(body, "inventory")["weight"] == 40
    # (35 x 60 + 25 x 100 + 20 x 60 + 40 x 100) / 120 = 81.7
    assert body["overall_score"] == 82 and body["coverage_pct"] == 120 * 100 // 130


def test_rules_whose_anchors_run_the_wrong_way_are_refused_by_the_database(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.add(HealthRule(
            category="sales", kpi_code="revenue", basis="value", direction="up_good",
            threshold_bad=D("10"), threshold_ok=D("5"), threshold_good=D("0"), weight=D("1"),
            industry_code="retail",
        ))  # fmt: skip
        db.flush()


def test_there_can_only_be_one_general_rule_per_kpi(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.add(HealthRule(
            category="financial", kpi_code="net_margin_pct", basis="value", direction="up_good",
            threshold_bad=D("0"), threshold_ok=D("1"), threshold_good=D("2"), weight=D("1"),
        ))  # fmt: skip
        db.flush()


def test_every_seeded_rule_points_at_a_real_kpi_in_the_right_area(db):
    rules = db.scalars(select(HealthRule).where(HealthRule.industry_code.is_(None))).all()
    kpis = {k.code: k for k in db.scalars(select(KpiDefinition))}
    assert len(rules) >= 14
    for rule in rules:
        assert rule.kpi_code in kpis, rule.kpi_code  # (an area and a KPI's own category can differ)
        assert rule.note, rule.kpi_code  # every threshold says where it came from


# --- reading it --------------------------------------------------------------------------


def test_before_anything_is_worked_out_there_is_no_score(api, business):
    res = get(api, business)
    assert res.status_code == 200 and res.json() is None
    assert get(api, business, "/history").json() == {"points": []}


def test_the_history_lists_every_finished_month_oldest_first(api, steady_shop):
    points = get(api, steady_shop, "/history").json()["points"]
    assert [p["period_start"] for p in points] == [f"2026-{m:02d}-01" for m in range(1, 9)]
    assert points[-1]["overall_score"] == 76 and points[-1]["trend"] == "up"
    last_two = get(api, steady_shop, "/history?limit=2").json()["points"]
    assert [p["period_start"] for p in last_two] == ["2026-07-01", "2026-08-01"]


def test_a_month_that_was_not_judged_is_not_found(api, steady_shop):
    res = get(api, steady_shop, "/2025-01-01")
    assert res.status_code == 404 and res.json()["error"]["code"] == "health_not_found"
    assert get(api, steady_shop, "/2026-08-15").status_code == 200  # any day finds its month
    assert get(api, steady_shop, "/not-a-date").status_code == 422


def test_viewers_can_see_the_score_and_each_business_only_its_own(api, db, steady_shop):
    assert get(api, steady_shop, who="viewer").status_code == 200
    assert get(api, steady_shop, who="other", org=1).json() is None  # the rival has none
    assert get(api, steady_shop, org=1).status_code == 404  # and Acme's owner can't read theirs
    with scoped(db, steady_shop, 1):
        assert db.scalar(select(func.count()).select_from(BusinessHealth)) == 0


def test_health_is_refreshed_whenever_the_kpis_are_worked_out(api, db, business, storage):
    from app.services import jobs
    from tests.test_kpi_engine import expense, sale

    for month in (1, 2, 3, 4):  # a few sales and costs, so the KPI engine has something to use
        sale(db, business, date(2026, month, 10), "100", cost="40")
        expense(db, business, date(2026, month, 5), "20")
    res = api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    assert res.status_code == 202
    ran = jobs.work_once(db, "health-worker", storage=storage)
    assert ran.status == "succeeded" and ran.kind == "kpi.calculate"
    latest = get(api, business).json()
    assert latest is not None and latest["coverage_pct"] > 0
    assert {c["category"] for c in latest["components"]} >= {"financial", "sales"}


def test_a_problem_working_out_health_does_not_hide_that_the_kpis_were_done(
    api, db, business, storage, monkeypatch
):
    from app.services import jobs
    from tests.test_kpi_engine import sale

    sale(db, business, date(2026, 1, 10), "100", cost="40")

    def boom(*args, **kwargs):
        raise RuntimeError("health broke")

    monkeypatch.setattr(health, "calculate", boom)
    api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    ran = jobs.work_once(db, "w", storage=storage)
    assert ran.status == "succeeded" and ran.result["status"] == "succeeded"
    assert api.get(f"{ORGS}/{business[0]}/kpis", headers=business[2]["owner"]).json()["last_run"]


# --- seasons: what "usual" means for a business that follows the trading year -------------


def season(name, start, end, pct, **extra):
    from app.models.business import BusinessSeason

    return BusinessSeason(
        name=name, start_month=start[0], start_day=start[1], end_month=end[0], end_day=end[1],
        expected_change_pct=None if pct is None else D(str(pct)), **extra,
    )  # fmt: skip


def test_an_earlier_month_is_stripped_of_its_season_and_the_answer_put_back_into_this_one():
    # Three 1,000 months at normal, then a month that is 40% busier: 1,000 x 1.4
    earlier = [(D("1000"), D("0")), (D("1400"), D("40")), (D("1400"), D("40"))]
    assert scoring.seasonal_baseline(earlier, D("40")) == D("1400")
    assert scoring.seasonal_baseline(earlier, D("0")) == D("1000")
    assert scoring.seasonal_baseline(earlier, D("-20")) == D("800")


def test_a_seasonal_usual_needs_enough_months_and_a_usable_season():
    assert scoring.seasonal_baseline([(D("1000"), D("0"))] * 2, D("0")) is None
    assert scoring.seasonal_baseline([(D("1000"), D("0"))] * 3, D("-100")) is None
    assert scoring.seasonal_baseline([(D("1000"), D("-100"))] * 3, D("0")) is None


def test_a_month_takes_the_seasons_that_cover_its_days_in_proportion():
    christmas = season("Christmas", (12, 1), (1, 5), 40)
    assert health.season_effect([christmas], date(2025, 12, 1)) == 40
    # 5 of January's 31 days: 40 x 5 / 31
    assert health.season_effect([christmas], date(2026, 1, 1)).quantize(D("0.01")) == D("6.45")
    assert health.season_effect([christmas], date(2026, 6, 1)) == 0
    assert health.season_effect([], date(2025, 12, 1)) == 0


def test_where_seasons_overlap_the_stronger_one_counts_for_that_day():
    sale = season("Sale", (9, 1), (9, 30), 20)
    slump = season("Slump", (9, 1), (9, 30), -50)
    assert health.season_effect([sale, slump], date(2026, 9, 1)) == -50


def sales_metric(body):
    return next(m for m in component(body, "sales")["metrics"] if m["kpi_code"] == "revenue")


def test_the_same_month_last_year_is_what_usual_means_once_there_is_a_year_of_history(
    api, db, business
):
    kpis = Kpis(db, business)
    kpis.put("revenue", date(2025, 8, 1), 2000)
    kpis.months("revenue", [1000] * 6 + [1800], start=2)  # Feb-Jul 1,000; August 1,800
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    # Against last August (2,000) that is 10% down: 30. Against the 1,000 average it would be 100.
    assert metric["baseline_kind"] == "last_year" and metric["baseline"] == "2000.00"
    assert metric["baseline_months"] == 1
    assert metric["compared_pct"] == "-10.0" and metric["score"] == 30
    assert "10% below the same month last year (£2,000.00)" in metric["text"]


def test_without_a_year_of_history_the_owners_seasons_adjust_the_average(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("revenue", [1000] * 6 + [1500], start=2)
    with scoped(db, business):
        db.add(season("Summer", (8, 1), (8, 31), 50, source="user", status="active"))
        db.flush()
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    # August is 50% busier than normal, so usual is 1,000 x 1.5 = 1,500; 1,500 is spot on (60).
    assert metric["baseline_kind"] == "seasonal" and metric["baseline"] == "1500.00"
    assert metric["baseline_months"] == 6 and metric["score"] == 60
    assert "adjusted for your busy and quiet seasons" in metric["text"]


def test_a_season_the_owner_has_not_confirmed_changes_nothing(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("revenue", [1000] * 6 + [1500], start=2)
    with scoped(db, business):
        db.add(season("Summer?", (8, 1), (8, 31), 50, source="detected", status="suggested"))
        db.add(season("Quiet", (8, 1), (8, 31), None, source="user", status="active"))
        db.flush()
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "average" and metric["baseline"] == "1000.00"
    assert metric["score"] == 100  # 50% above usual


def test_last_year_beats_the_seasons_because_it_already_contains_them(api, db, business):
    kpis = Kpis(db, business)
    kpis.put("revenue", date(2025, 8, 1), 1500)
    kpis.months("revenue", [1000] * 6 + [1500], start=2)
    with scoped(db, business):
        db.add(season("Summer", (8, 1), (8, 31), 50, source="user", status="active"))
        db.flush()
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "last_year" and metric["baseline"] == "1500.00"


def test_a_figure_that_does_not_follow_the_trading_year_ignores_seasons(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("average_order_value", [50] * 7, start=2)
    kpis.put("average_order_value", date(2025, 8, 1), 80)
    with scoped(db, business):
        db.add(season("Summer", (8, 1), (8, 31), 50, source="user", status="active"))
        db.flush()
    calculate(db, business)
    body = get(api, business, "/2026-08-01").json()
    metric = next(
        m for m in component(body, "sales")["metrics"] if m["kpi_code"] == "average_order_value"
    )
    assert metric["baseline_kind"] == "average" and metric["baseline"] == "50.00"


def test_a_rule_can_be_made_seasonal_or_not_as_data(api, db, business):
    kpis = Kpis(db, business)
    kpis.put("revenue", date(2025, 8, 1), 2000)
    kpis.months("revenue", [1000] * 6 + [1800], start=2)
    db.execute(
        HealthRule.__table__.update().where(HealthRule.kpi_code == "revenue").values(seasonal=False)
    )
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "average" and metric["baseline"] == "1000.00"


def test_a_seasonal_rule_with_no_season_and_no_last_year_is_the_plain_average(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("revenue", [1000] * 6 + [1100], start=2)
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "average" and metric["score"] == 100
    assert "the average of the last 6 months)" in metric["text"]


def test_a_value_rule_has_no_kind_of_usual(api, steady_shop):
    body = get(api, steady_shop).json()
    margin = next(m for m in component(body, "financial")["metrics"] if m["basis"] == "value")
    assert margin["baseline_kind"] is None


def test_a_last_year_of_nothing_is_not_used_as_usual(api, db, business):
    kpis = Kpis(db, business)
    kpis.put("revenue", date(2025, 8, 1), 0)
    kpis.months("revenue", [1000] * 6 + [1100], start=2)
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "average" and metric["baseline"] == "1000.00"


def test_seasons_in_other_months_do_not_change_how_usual_is_worked_out(api, db, business):
    kpis = Kpis(db, business)
    kpis.months("revenue", [1000] * 6 + [1100], start=2)
    with scoped(db, business):
        db.add(season("Christmas", (12, 1), (12, 31), 40, source="user", status="active"))
        db.flush()
    calculate(db, business)
    metric = sales_metric(get(api, business, "/2026-08-01").json())
    assert metric["baseline_kind"] == "average" and metric["baseline"] == "1000.00"
