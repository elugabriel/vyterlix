"""More figures to forecast: demand, new customers, and keeping customers (a percentage)."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.forecast import Forecast
from app.services import forecast
from tests.test_detection import Values
from tests.test_forecast import ORGS, make, months_back, owner_tenant, scoped, season

CODES = [
    "revenue", "sales_count", "units_sold", "active_customers", "new_customers",
    "customer_retention_pct", "customer_churn_pct",
]  # fmt: skip


def put(db, business, code, values, end=(2026, 9)):
    series = Values(db, business)
    for month, value in zip(months_back(len(values), end), values, strict=True):
        series.put(code, month, value, None)
    return series


def test_the_figures_that_can_be_forecast_are_named_and_grouped(api, business):
    body = api.get(f"{ORGS}/{business[0]}/forecasts", headers=business[2]["owner"]).json()
    assert body["kpis"] == CODES
    figures = {f["code"]: f for f in body["figures"]}
    assert [f["code"] for f in body["figures"]] == CODES
    assert figures["revenue"] == {
        "code": "revenue",
        "name": "Sales",
        "unit": "gbp",
        "group": "Sales",
    }
    assert figures["customer_retention_pct"]["unit"] == "percent"
    assert {f["group"] for f in body["figures"]} == {
        "Sales",
        "Customer demand",
        "Keeping customers",
    }
    assert [f["group"] for f in body["figures"]].count("Customer demand") == 4


@pytest.mark.parametrize("code", ["sales_count", "units_sold", "active_customers", "new_customers"])
def test_demand_is_forecast_like_sales_is(api, db, business, code):
    put(db, business, code, [100] * 12)
    body = make(api, business, code=code).json()
    assert body["kpi_code"] == code and body["status"] == "ok"
    # No misses at all, so the range is 2% of the level: 2 x 1.2816 = 2.56 each side of 100
    assert [(p["value"], p["lower"], p["upper"]) for p in body["predictions"]][0] == (
        "100.00", "97.44", "102.56",
    )  # fmt: skip
    assert body["explanation"].startswith(f"We expect {body['kpi_name']} of about 100")


def test_a_percentage_cannot_be_forecast_past_a_hundred(api, db, business):
    put(db, business, "customer_retention_pct", list(range(88, 100)))  # 88 up to 99
    body = make(api, business, code="customer_retention_pct").json()
    # The line would run on to 100, 101, 102: held at 100, and so is the top of every range
    assert [p["value"] for p in body["predictions"]] == ["100.00", "100.00", "100.00"]
    assert all(p["upper"] == "100.00" for p in body["predictions"])
    assert all(float(p["lower"]) <= 100.0 for p in body["predictions"])


def test_the_ceiling_is_held_when_testing_the_methods_too(db, business):
    from app.forecast import selection
    from app.forecast.models import LinearTrend

    climbing = [float(v) for v in range(88, 100)] + [100.0, 100.0]
    held = selection.backtest(LinearTrend(), climbing, non_negative=True, ceiling=100.0)
    free = selection.backtest(LinearTrend(), climbing, non_negative=True, ceiling=None)
    assert held.mae < free.mae


def test_a_percentage_cannot_be_forecast_below_nothing(api, db, business):
    put(db, business, "customer_churn_pct", list(range(12, 0, -1)))  # 12 down to 1
    body = make(api, business, code="customer_churn_pct").json()
    assert body["predictions"][0]["value"] == "0.00" and body["predictions"][0]["lower"] == "0.00"


def test_a_percentage_in_the_middle_is_forecast_as_it_is(api, db, business):
    put(db, business, "customer_retention_pct", [60] * 12)
    body = make(api, business, code="customer_retention_pct").json()
    assert [(p["value"], p["lower"], p["upper"]) for p in body["predictions"]][0] == (
        "60.00", "58.46", "61.54",
    )  # fmt: skip


def christmas(db, business):
    with scoped(db, business):
        db.add(season("Christmas", (12, 1), (12, 31), 50, source="user", status="active"))
        db.flush()


def test_demand_follows_the_trading_year_but_a_retention_rate_does_not(api, db, business):
    flat = [100] * 12
    flat[2] = 150  # December 2025
    put(db, business, "sales_count", flat)
    put(db, business, "customer_retention_pct", flat)
    christmas(db, business)
    demand = make(api, business, code="sales_count").json()
    assert demand["adjusted_for_seasons"] is True
    assert [p["value"] for p in demand["predictions"]] == ["100.00", "100.00", "150.00"]
    keeping = make(api, business, code="customer_retention_pct").json()
    assert keeping["adjusted_for_seasons"] is False
    assert [p["value"] for p in keeping["predictions"]] == ["100.00"] * 3


def test_each_figure_is_forecast_on_its_own_figures(api, db, business):
    put(db, business, "sales_count", [100] * 12)
    put(db, business, "new_customers", [20] * 12)
    assert make(api, business, code="sales_count").json()["predictions"][0]["value"] == "100.00"
    assert make(api, business, code="new_customers").json()["predictions"][0]["value"] == "20.00"
    assert (
        api.get(f"{ORGS}/{business[0]}/forecasts/revenue", headers=business[2]["owner"]).json()
        is None
    )  # nothing was said about sales


def test_forecasting_everything_does_every_figure_that_has_history(db, business):
    put(db, business, "revenue", [1000] * 12)
    put(db, business, "sales_count", [100] * 12)
    put(db, business, "customer_retention_pct", [60] * 12)
    with scoped(db, business):
        result = forecast.calculate_all(db, owner_tenant(db, business))
        assert (result.forecasts, result.forecast_months) == (3, 9)
        assert db.scalar(select(func.count()).select_from(Forecast)) == 3


def test_every_figure_has_its_own_accuracy(api, db, business):
    series = put(db, business, "sales_count", [100] * 12)
    make(api, business, code="sales_count")
    for month, value in ((10, 101), (11, 99), (12, 100)):
        series.put("sales_count", date(2026, month, 1), value, None)
    with scoped(db, business):
        forecast.refresh_actuals(db, owner_tenant(db, business))
    body = api.get(
        f"{ORGS}/{business[0]}/forecasts/sales_count/accuracy", headers=business[2]["owner"]
    ).json()
    assert (body["kpi_code"], body["checked"], body["unit"]) == ("sales_count", 3, "count")
    revenue = api.get(
        f"{ORGS}/{business[0]}/forecasts/revenue/accuracy", headers=business[2]["owner"]
    ).json()
    assert revenue["checked"] == 0


def test_a_figure_that_is_not_on_the_list_is_still_refused(api, business):
    res = make(api, business, code="net_profit")
    assert res.status_code == 422 and res.json()["error"]["code"] == "bad_metric"
    _ = months_back


def test_a_season_that_says_everything_stops_cannot_be_used_so_the_forecast_is_left_alone(
    api, db, business
):
    put(db, business, "sales_count", [100] * 12)
    with scoped(db, business):
        db.add(season("Closed", (12, 1), (12, 31), -100, source="user", status="active"))
        db.flush()
    body = make(api, business, code="sales_count").json()
    assert body["adjusted_for_seasons"] is False
    assert [p["value"] for p in body["predictions"]] == ["100.00"] * 3


def test_a_season_that_changes_nothing_does_not_count_as_allowing_for_seasons(api, db, business):
    put(db, business, "sales_count", [100] * 12)
    with scoped(db, business):
        db.add(season("Same as ever", (12, 1), (12, 31), 0, source="user", status="active"))
        db.flush()
    assert make(api, business, code="sales_count").json()["adjusted_for_seasons"] is False
