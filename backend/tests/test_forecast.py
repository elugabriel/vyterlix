"""Forecasting: the methods and choosing one (pure), then whole forecasts worked out by hand."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.forecast import selection
from app.forecast.models import METHODS, LinearTrend, MovingAverage, SeasonalNaive
from app.models.forecast import (
    Forecast,
    ForecastEvaluation,
    ForecastModel,
    ForecastPrediction,
)
from app.models.kpi import KpiDefinition
from app.services import forecast
from tests.test_detection import Values
from tests.test_health import ORGS, owner_tenant, scoped, season

D = Decimal


def approx(value, expected, places=6):
    assert abs(value - expected) < 10**-places, (value, expected)


# --- the methods ----------------------------------------------------------------------------


def test_the_recent_average_expects_the_next_months_to_look_like_the_last_three():
    assert MovingAverage().predict([10.0, 20.0, 30.0, 40.0], 3) == [30.0, 30.0, 30.0]
    assert MovingAverage().predict([5.0, 7.0], 1) == [6.0]  # with fewer than three, all of them


def test_a_straight_line_is_carried_forward():
    assert LinearTrend().predict([10.0, 20.0, 30.0, 40.0, 50.0], 2) == pytest.approx([60.0, 70.0])
    assert LinearTrend().predict([50.0, 40.0, 30.0, 20.0], 2) == pytest.approx([10.0, 0.0])
    assert LinearTrend().predict([7.0, 7.0, 7.0, 7.0], 3) == pytest.approx([7.0, 7.0, 7.0])


def test_the_trend_only_looks_at_the_last_twelve_months():
    history = [1000.0] * 5 + [float(v) for v in range(100, 1300, 100)]  # then a clean climb
    assert LinearTrend().predict(history, 1) == pytest.approx([1300.0])


def test_a_trend_that_is_not_perfectly_straight_is_a_best_fit():
    # 10, 30, 20, 40: slope 8, and the line passes through the mean (25) at x = 1.5, so 13 at x = 0
    assert LinearTrend().predict([10.0, 30.0, 20.0, 40.0], 1) == pytest.approx([45.0])


def test_the_same_month_last_year_repeats_the_year_before():
    history = [float(v) for v in range(1, 15)]  # 14 months; the last twelve are 3..14
    assert SeasonalNaive().predict(history, 3) == [3.0, 4.0, 5.0]
    assert SeasonalNaive().predict(history, 13)[12] == 3.0  # a second year repeats the cycle


def test_the_methods_are_registered_simplest_first():
    assert list(METHODS) == ["moving_average", "linear_trend", "seasonal_naive"]
    assert [m.min_history for m in METHODS.values()] == [3, 4, 12]


def test_the_methods_in_code_match_the_methods_in_the_database(db):
    rows = {m.code: m for m in db.scalars(select(ForecastModel))}
    assert set(rows) == set(METHODS)
    for code, method in METHODS.items():
        assert rows[code].min_history == method.min_history and rows[code].version == "1"
        assert rows[code].is_active and rows[code].description and rows[code].name


# --- trying them out ------------------------------------------------------------------------


def test_nothing_can_be_tested_with_under_six_months():
    assert selection.eligible([1.0] * 5) == []
    assert [m.code for m in selection.eligible([1.0] * 6)] == ["moving_average"]


@pytest.mark.parametrize(
    ("months", "tested", "methods"),
    [
        (6, 3, ["moving_average"]),
        (9, 6, ["moving_average"]),  # the first test month has only 3 months before it
        (10, 6, ["moving_average", "linear_trend"]),
        (17, 6, ["moving_average", "linear_trend"]),
        (18, 6, ["moving_average", "linear_trend", "seasonal_naive"]),
    ],
)
def test_a_method_is_only_tried_when_it_has_enough_months_before_the_first_test(
    months, tested, methods
):
    history = [1.0] * months
    assert selection.test_months(months) == tested
    assert [m.code for m in selection.eligible(history)] == methods


def test_a_method_is_marked_by_forecasting_months_it_has_not_seen():
    # 100..150 climbing by 10: the recent average lags by 20 every time
    score = selection.backtest(MovingAverage(), [100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
    assert score.errors == pytest.approx([20.0, 20.0, 20.0])
    assert (score.mae, score.rmse, score.n_points) == (pytest.approx(20.0), pytest.approx(20.0), 3)
    approx(score.mape, (20 / 130 + 20 / 140 + 20 / 150) / 3 * 100, places=4)


def test_misses_of_different_sizes_are_scored_by_typical_size_and_by_the_big_ones():
    score = selection.backtest(MovingAverage(), [10.0, 10.0, 10.0, 10.0, 10.0, 40.0, 10.0, 10.0])
    # tested on the last 5: errors 0, 0, 30 (the spike), then the average has 40 in it
    assert len(score.errors) == 5
    assert score.mae == pytest.approx(sum(abs(e) for e in score.errors) / 5)
    assert score.rmse > score.mae  # the big miss counts for more


def test_a_month_of_nothing_is_left_out_of_the_percentage_but_not_the_pounds():
    score = selection.backtest(MovingAverage(), [5.0, 5.0, 5.0, 5.0, 5.0, 0.0])
    assert score.errors[-1] == -5.0 and score.mae > 0
    assert score.mape == pytest.approx(0.0)  # only the months that were not zero count
    assert selection.backtest(MovingAverage(), [0.0] * 6).mape is None


def test_a_forecast_below_nothing_is_held_at_nothing_when_testing_too():
    falling = [40.0, 30.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.0, 0.0, 0.0]
    clamped = selection.backtest(LinearTrend(), falling, non_negative=True)
    free = selection.backtest(LinearTrend(), falling, non_negative=False)
    assert clamped.mae < free.mae


def test_the_closest_method_wins_and_a_tie_goes_to_the_simplest():
    a = selection.Score("linear_trend", 5.0, 5.0, None, 6, [])
    b = selection.Score("moving_average", 5.0, 9.0, None, 6, [])
    c = selection.Score("seasonal_naive", 4.0, 9.0, None, 6, [])
    assert selection.choose([a, b]).code == "moving_average"
    assert selection.choose([a, b, c]).code == "seasonal_naive"
    assert selection.choose([]) is None


def test_a_perfect_line_is_best_forecast_with_a_line():
    history = [float(v) for v in range(10, 110, 10)]  # 10 months, +10 each
    scores = [selection.backtest(m, history) for m in selection.eligible(history)]
    best = selection.choose(scores)
    assert best.code == "linear_trend" and best.mae == pytest.approx(0.0, abs=1e-9)


# --- how far to trust it --------------------------------------------------------------------


def test_the_range_is_as_wide_as_the_method_usually_misses():
    score = selection.Score("moving_average", 80.0, 100.0, None, 6, [])
    assert selection.spread(score, [1000.0] * 6) == 100.0


def test_a_range_is_never_narrower_than_two_per_cent_of_the_level():
    score = selection.Score("moving_average", 0.0, 0.0, None, 6, [])
    assert selection.spread(score, [900.0, 1000.0, 1100.0, 1000.0, 1000.0, 1000.0]) == 20.0
    assert selection.spread(score, [-1000.0] * 6) == 20.0  # a loss is judged by its size


def test_the_range_widens_with_the_square_root_of_the_months_ahead():
    lower, upper = selection.interval(1000.0, 100.0, 1)
    assert (lower, upper) == (pytest.approx(871.84), pytest.approx(1128.16))  # 1.2816 x 100
    lower, upper = selection.interval(1000.0, 100.0, 4)
    assert (lower, upper) == (pytest.approx(743.68), pytest.approx(1256.32))  # twice as wide


@pytest.mark.parametrize(
    ("level", "half"), [(50, 67.45), (60, 84.16), (70, 103.64), (80, 128.16), (90, 164.49),
                        (95, 196.0), (99, 257.58)],
)  # fmt: skip
def test_a_more_certain_range_is_wider(level, half):
    lower, upper = selection.interval(1000.0, 100.0, 1, level)
    assert upper - 1000.0 == pytest.approx(half, abs=0.01) and 1000.0 - lower == pytest.approx(
        half, abs=0.01
    )


def test_a_range_stops_at_nothing_for_figures_that_cannot_go_negative():
    assert selection.interval(50.0, 100.0, 1)[0] == 0.0
    assert selection.interval(50.0, 100.0, 1, non_negative=False)[0] == pytest.approx(-78.16)


# --- whole forecasts on figures worked out by hand ----------------------------------------


def months_back(count, end=(2026, 9)):
    """The first days of `count` months ending at `end`, oldest first."""
    year, month = end
    found = []
    for _ in range(count):
        found.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(found))


def put_revenue(db, business, values, end=(2026, 9), **kwargs):
    series = Values(db, business)
    for month, value in zip(months_back(len(values), end), values, strict=True):
        series.put("revenue", month, value, None, **kwargs)
    return series


def make(api, business, query="", who="owner", org=0, code="revenue"):
    return api.post(f"{ORGS}/{business[org]}/forecasts/{code}{query}", headers=business[2][who])


def latest(api, business, who="owner", org=0, code="revenue"):
    res = api.get(f"{ORGS}/{business[org]}/forecasts/{code}", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def money(prediction):
    return (prediction["value"], prediction["lower"], prediction["upper"])


def test_a_steady_business_is_forecast_to_stay_steady_with_a_range_that_grows(api, db, business):
    put_revenue(db, business, [1000] * 12)
    res = make(api, business)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok" and body["as_of"] == "2026-09-01" and body["horizon"] == 3
    assert body["interval_level"] == 80 and body["history_months"] == 12
    assert body["method"]["code"] == "moving_average"  # level with the line, so the simpler wins
    # No misses at all, so the range is the 2% floor: 20 x 1.2816 x the square root of months ahead
    assert [money(p) for p in body["predictions"]] == [
        ("1000.00", "974.37", "1025.63"),
        ("1000.00", "963.75", "1036.25"),
        ("1000.00", "955.60", "1044.40"),
    ]
    assert [p["period_start"] for p in body["predictions"]] == [
        "2026-10-01",
        "2026-11-01",
        "2026-12-01",
    ]
    assert [p["period_end"] for p in body["predictions"]] == [
        "2026-10-31",
        "2026-11-30",
        "2026-12-31",
    ]
    assert all(p["actual_value"] is None for p in body["predictions"])


def test_steady_growth_is_forecast_to_continue(api, db, business):
    put_revenue(db, business, [1000 + 100 * i for i in range(12)])  # 1,000 to 2,100
    body = make(api, business).json()
    assert body["method"]["code"] == "linear_trend"
    # A perfect line: no misses, so the 2% floor of the level (2,000): 40 x 1.2816 = 51.26
    assert [money(p) for p in body["predictions"]] == [
        ("2200.00", "2148.74", "2251.26"),
        ("2300.00", "2227.50", "2372.50"),
        ("2400.00", "2311.21", "2488.79"),
    ]


def test_a_year_that_repeats_is_forecast_by_the_same_months_last_year(api, db, business):
    year = [500, 300, 300, 400, 600, 700, 900, 800, 600, 400, 300, 1200]
    put_revenue(db, business, year * 2)  # two years, October 2024 to September 2026
    body = make(api, business).json()
    assert body["method"]["code"] == "seasonal_naive" and body["history_months"] == 24
    # October, November, December repeat the pattern: 500, 300, 300. Floor 2% of 633.33.
    assert [money(p) for p in body["predictions"]][0] == ("500.00", "483.77", "516.23")
    assert [p["value"] for p in body["predictions"]] == ["500.00", "300.00", "300.00"]
    kinds = {e["method"]["code"]: e for e in body["evaluations"]}
    assert set(kinds) == {"moving_average", "linear_trend", "seasonal_naive"}
    assert kinds["seasonal_naive"]["typical_miss"] == "0.00" and kinds["seasonal_naive"]["chosen"]


def test_the_forecast_says_why_it_chose_that_method_and_how_far_off_it_has_been(api, db, business):
    put_revenue(db, business, [1000 + 100 * i for i in range(12)])
    body = make(api, business).json()
    by_code = {e["method"]["code"]: e for e in body["evaluations"]}
    assert set(by_code) == {"moving_average", "linear_trend"}
    # Tried on the last 6 months: the average lagged a steady climb by 200 every time
    assert by_code["moving_average"]["typical_miss"] == "200.00"
    assert (
        by_code["moving_average"]["months_tested"] == 6 and not by_code["moving_average"]["chosen"]
    )
    assert by_code["linear_trend"]["typical_miss"] == "0.00" and by_code["linear_trend"]["chosen"]
    assert [e["method"]["code"] for e in body["evaluations"]] == ["linear_trend", "moving_average"]
    assert body["explanation"].startswith("We expect Sales of about £2,200.00 in October 2026")
    assert "most likely between £2,148.74 and £2,251.26" in body["explanation"]
    assert 'The method is "Straight-line trend"' in body["explanation"]
    assert "last 6 months, where it missed by about £0.00 (0%) on average" in body["explanation"]
    assert "80 times out of 100" in body["explanation"]


def test_the_history_the_forecast_learned_from_comes_back_with_it(api, db, business):
    put_revenue(db, business, [1000 + 100 * i for i in range(12)])
    body = make(api, business).json()
    assert [h["period_start"] for h in body["history"]][0] == "2025-10-01"
    assert [h["value"] for h in body["history"]][-1] == "2100.00"
    assert len(body["history"]) == 12


def test_a_forecast_never_goes_below_nothing(api, db, business):
    put_revenue(db, business, [1200 - 100 * i for i in range(12)])  # 1,200 down to 100
    body = make(api, business).json()
    assert body["method"]["code"] == "linear_trend"
    # The line would reach 0, -100, -200: held at nothing, with a range that starts at nothing
    assert [money(p)[:2] for p in body["predictions"]] == [("0.00", "0.00")] * 3
    assert body["predictions"][0]["upper"] == "5.13"  # 2% of the level (200) = 4; x 1.2816


def test_a_busier_christmas_is_not_mistaken_for_growth(api, db, business):
    values = [1000] * 12
    values[2] = 1500  # December 2025
    put_revenue(db, business, values)
    with scoped(db, business):
        db.add(season("Christmas", (12, 1), (12, 31), 50, source="user", status="active"))
        db.flush()
    body = make(api, business).json()
    assert body["adjusted_for_seasons"] is True
    # With December's season taken out the history is flat; December 2026 is put back at +50%
    assert [p["value"] for p in body["predictions"]] == ["1000.00", "1000.00", "1500.00"]
    assert money(body["predictions"][2]) == ("1500.00", "1433.41", "1566.59")


def test_a_busier_christmas_without_the_season_entered_looks_like_a_blip(api, db, business):
    values = [1000] * 12
    values[2] = 1500
    put_revenue(db, business, values)
    body = make(api, business).json()
    assert body["adjusted_for_seasons"] is False
    assert [p["value"] for p in body["predictions"]] == ["1000.00"] * 3


def test_a_season_the_owner_has_not_confirmed_changes_nothing(api, db, business):
    put_revenue(db, business, [1000] * 12)
    with scoped(db, business):
        db.add(season("Summer?", (12, 1), (12, 31), 50, source="detected", status="suggested"))
        db.flush()
    body = make(api, business).json()
    assert body["adjusted_for_seasons"] is False


def test_fewer_than_six_months_is_not_enough_and_says_so(api, db, business):
    put_revenue(db, business, [1000, 1100, 1200, 1300, 1400])
    body = make(api, business).json()
    assert body["status"] == "insufficient_data" and body["predictions"] == []
    assert body["method"] is None and body["evaluations"] == []
    assert body["explanation"] == (
        "We need at least 6 finished months of Sales to forecast it, and have 5 so far."
    )
    assert body["history_months"] == 5


def test_exactly_six_months_is_enough(api, db, business):
    put_revenue(db, business, [1000] * 6)
    body = make(api, business).json()
    assert body["status"] == "ok" and body["method"]["code"] == "moving_average"
    assert [e["months_tested"] for e in body["evaluations"]] == [3]


def test_nothing_to_learn_from_is_not_found(api, business):
    res = make(api, business)
    assert res.status_code == 404 and res.json()["error"]["code"] == "no_history"
    assert latest(api, business) is None


def test_a_gap_in_the_figures_means_only_what_came_after_it_is_used(api, db, business):
    series = Values(db, business)
    for month, value in zip(
        months_back(10), [5000, 5000, 5000, 1000, 1000, 1000, 1000, 1000, 1000, 1000], strict=True
    ):
        if month != date(2026, 3, 1):  # March is missing
            series.put("revenue", month, value, None)
    body = make(api, business).json()
    assert body["history_months"] == 6  # April to September; the older months are cut off
    assert [p["value"] for p in body["predictions"]] == ["1000.00"] * 3


def test_a_month_that_could_not_be_worked_out_breaks_the_run_too(api, db, business):
    series = Values(db, business)
    for month, value in zip(
        months_back(9), [9, 9, 9, 1000, 1000, 1000, 1000, 1000, 1000], strict=True
    ):
        if month == date(2026, 1, 1):
            series.put("revenue", month, None, None, status="undefined")
        else:
            series.put("revenue", month, value, None)
    assert make(api, business).json()["history_months"] == 8


def test_a_month_still_in_progress_is_not_learned_from(api, db, business):
    put_revenue(db, business, [1000] * 11, end=(2026, 8))  # October 2025 to August 2026
    series = Values(db, business)
    series.put("revenue", date(2026, 9, 1), 50, None, complete=False)
    body = make(api, business).json()
    assert body["as_of"] == "2026-08-01"  # the forecast starts from the last finished month
    assert [p["period_start"] for p in body["predictions"]][0] == "2026-09-01"
    assert body["predictions"][0]["value"] == "1000.00"


def test_the_number_of_months_and_how_sure_the_range_is_can_be_chosen(api, db, business):
    put_revenue(db, business, [1000] * 12)
    body = make(api, business, "?horizon=5&level=95").json()
    assert body["horizon"] == 5 and len(body["predictions"]) == 5 and body["interval_level"] == 95
    # 1.96 x 20 = 39.20 each side at one month ahead
    assert money(body["predictions"][0]) == ("1000.00", "960.80", "1039.20")
    assert "95 times out of 100" in body["explanation"]
    one = make(api, business, "?horizon=1").json()
    assert len(one["predictions"]) == 1


def test_a_choice_that_makes_no_sense_is_refused(api, db, business):
    put_revenue(db, business, [1000] * 12)
    assert make(api, business, "?horizon=0").status_code == 422
    assert make(api, business, "?horizon=13").status_code == 422
    level = make(api, business, "?level=85")
    assert level.status_code == 422 and level.json()["error"]["code"] == "bad_level"
    kpi = make(api, business, code="net_profit")
    assert kpi.status_code == 422 and kpi.json()["error"]["code"] == "bad_metric"
    assert (
        api.get(
            f"{ORGS}/{business[0]}/forecasts/net_profit", headers=business[2]["owner"]
        ).status_code
        == 422
    )


def test_the_figures_that_can_be_forecast_are_listed(api, business):
    res = api.get(f"{ORGS}/{business[0]}/forecasts", headers=business[2]["owner"])
    assert res.json()["kpis"][0] == "revenue" and len(res.json()["kpis"]) == 7


def test_forecasting_again_from_the_same_month_replaces_it(api, db, business):
    put_revenue(db, business, [1000] * 12)
    first = make(api, business).json()
    second = make(api, business, "?horizon=2").json()
    assert second["id"] != first["id"] and len(second["predictions"]) == 2
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Forecast)) == 1
        assert db.scalar(select(func.count()).select_from(ForecastPrediction)) == 2
        assert db.scalar(select(func.count()).select_from(ForecastEvaluation)) == 2


def test_a_new_finished_month_gives_a_new_forecast_and_the_old_one_is_kept_for_checking(
    api, db, business
):
    series = put_revenue(db, business, [1000] * 12)
    first = make(api, business).json()
    series.put("revenue", date(2026, 10, 1), 1000, None)
    second = make(api, business).json()
    assert (first["as_of"], second["as_of"]) == ("2026-09-01", "2026-10-01")
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Forecast)) == 2
    assert latest(api, business)["id"] == second["id"]  # the newest is the one shown


def test_the_forecast_records_which_method_and_version_made_it_and_what_it_learned_from(
    api, db, business
):
    put_revenue(db, business, [1000 + 100 * i for i in range(12)])
    make(api, business)
    with scoped(db, business):
        stored = db.scalars(select(Forecast)).one()
        model = db.get(ForecastModel, stored.model_id)
        assert (model.code, stored.model_version) == ("linear_trend", "1")
        assert D(stored.inputs["values"][-1]) == 2100 and len(stored.inputs["months"]) == 12
        assert stored.inputs["non_negative"] is True and stored.inputs["sigma"] == 40.0


def test_a_forecast_can_be_read_back_after_it_is_made(api, db, business):
    put_revenue(db, business, [1000] * 12)
    made = make(api, business).json()
    assert latest(api, business) == made


def test_viewers_can_read_a_forecast_but_not_make_one(api, db, business):
    put_revenue(db, business, [1000] * 12)
    assert make(api, business, who="viewer").status_code == 403
    assert latest(api, business, who="viewer") is None
    made = make(api, business).json()
    assert latest(api, business, who="viewer") == made
    url = f"{ORGS}/{business[0]}/forecasts/revenue"
    assert api.get(url).status_code == 401 and api.post(url).status_code == 401


def test_each_business_forecasts_only_from_its_own_figures(api, db, business):
    put_revenue(db, business, [1000] * 12)
    make(api, business)
    assert latest(api, business, who="other", org=1) is None
    assert make(api, business, who="other", org=1).status_code == 404  # the rival has no history
    res = api.get(f"{ORGS}/{business[1]}/forecasts/revenue", headers=business[2]["owner"])
    assert res.status_code == 404
    with scoped(db, business, 1):
        assert db.scalar(select(func.count()).select_from(Forecast)) == 0


# --- the forecast is refreshed whenever the figures are --------------------------------------


def test_forecasts_are_refreshed_whenever_the_kpis_are_worked_out(api, db, business, storage):
    from app.services import jobs
    from tests.test_kpi_engine import sale

    for month in range(1, 9):  # January to August 2026
        sale(db, business, date(2026, month, 10), "1000", cost="400")
    api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    ran = jobs.work_once(db, "forecast-worker", storage=storage)
    assert ran.status == "succeeded" and ran.kind == "kpi.calculate"
    body = latest(api, business)
    assert body is not None and body["status"] == "ok"
    assert body["as_of"] == "2026-08-01" or body["as_of"] > "2026-08-01"
    assert body["history_months"] >= 8
    assert len(body["predictions"]) == 3


def test_a_problem_forecasting_does_not_hide_that_the_kpis_were_done(
    api, db, business, storage, monkeypatch
):
    from app.services import jobs
    from tests.test_kpi_engine import sale

    sale(db, business, date(2026, 1, 10), "100", cost="40")

    def boom(*args, **kwargs):
        raise RuntimeError("forecast broke")

    monkeypatch.setattr(forecast, "calculate_all", boom)
    api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    ran = jobs.work_once(db, "w", storage=storage)
    assert ran.status == "succeeded" and ran.result["status"] == "succeeded"


# --- the tables -----------------------------------------------------------------------------


def _forecast_row(db, business, **extra):
    with scoped(db, business):
        kpi_id = db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == "revenue")).one()
        model_id = db.scalars(
            select(ForecastModel.id).where(ForecastModel.code == "moving_average")
        ).one()
        fields = dict(
            kpi_id=kpi_id, as_of=date(2026, 9, 1), horizon=3, status="ok", model_id=model_id,
            interval_level=80, history_months=12, explanation="x",
            calculated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )  # fmt: skip
        row = Forecast(**{**fields, **extra})
        db.add(row)
        db.flush()
        return row.id


def _prediction(db, business, forecast_id, **extra):
    with scoped(db, business):
        fields = dict(
            forecast_id=forecast_id, period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
            value=D("100"), lower_value=D("90"), upper_value=D("110"),
        )  # fmt: skip
        db.add(ForecastPrediction(**{**fields, **extra}))
        db.flush()


@pytest.mark.parametrize(
    "bad",
    [{"horizon": 0}, {"horizon": 13}, {"interval_level": 49}, {"interval_level": 100},
     {"status": "maybe"}, {"history_months": -1}, {"granularity": "week"},
     {"model_id": None}],
)  # fmt: skip
def test_a_forecast_that_makes_no_sense_is_refused(db, business, bad):
    with pytest.raises(IntegrityError):
        _forecast_row(db, business, **bad)


def test_a_forecast_with_too_little_history_needs_no_method(db, business):
    _forecast_row(db, business, status="insufficient_data", model_id=None, model_version=None)


def test_there_is_one_forecast_per_figure_and_last_month(db, business):
    _forecast_row(db, business)
    with pytest.raises(IntegrityError):
        _forecast_row(db, business)


def test_a_prediction_must_sit_inside_its_own_range(db, business):
    forecast_id = _forecast_row(db, business)
    _prediction(db, business, forecast_id)
    with pytest.raises(IntegrityError):
        _prediction(
            db,
            business,
            forecast_id,
            period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 30),
            value=D("120"),
        )  # above its own upper range


def test_a_prediction_cannot_end_before_it_starts(db, business):
    forecast_id = _forecast_row(db, business)
    with pytest.raises(IntegrityError):
        _prediction(
            db, business, forecast_id, period_start=date(2026, 12, 1), period_end=date(2026, 11, 30)
        )


def test_a_month_can_only_be_predicted_once_per_forecast(db, business):
    forecast_id = _forecast_row(db, business)
    _prediction(db, business, forecast_id)
    with pytest.raises(IntegrityError):
        _prediction(db, business, forecast_id)


def test_predictions_cannot_be_attached_to_another_businesss_forecast(db, business):
    forecast_id = _forecast_row(db, business)
    with pytest.raises(IntegrityError), scoped(db, business, 1):
        db.add(
            ForecastPrediction(
                forecast_id=forecast_id, period_start=date(2026, 10, 1),
                period_end=date(2026, 10, 31), value=D("1"), lower_value=D("1"), upper_value=D("1"),
            )
        )  # fmt: skip
        db.flush()


def test_removing_a_forecast_removes_its_predictions_and_evaluations(db, business):
    forecast_id = _forecast_row(db, business)
    _prediction(db, business, forecast_id)
    with scoped(db, business):
        db.add(
            ForecastEvaluation(
                forecast_id=forecast_id, model_code="moving_average", method="backtest",
                mae=D("1"), rmse=D("1"), n_points=3,
            )
        )  # fmt: skip
        db.flush()
        db.execute(Forecast.__table__.delete())
        assert db.scalar(select(func.count()).select_from(ForecastPrediction)) == 0
        assert db.scalar(select(func.count()).select_from(ForecastEvaluation)) == 0


def test_an_evaluation_must_make_sense(db, business):
    forecast_id = _forecast_row(db, business)
    for bad in ({"method": "guess"}, {"n_points": 0}, {"mae": D("-1")}):
        with pytest.raises(IntegrityError), scoped(db, business):
            fields = dict(model_code="moving_average", method="backtest", mae=D("1"),
                          rmse=D("1"), n_points=3)  # fmt: skip
            db.add(ForecastEvaluation(forecast_id=forecast_id, **{**fields, **bad}))
            db.flush()
        db.rollback()
        forecast_id = _forecast_row(db, business, as_of=date(2026, 8, 1))


def test_the_forecast_is_never_asked_of_a_language_model(api, db, business):
    # Every number comes from app.forecast; nothing in the service reaches out to an AI provider.
    import app.services.forecast as service

    source = open(service.__file__, encoding="utf-8").read().lower()
    assert (
        "anthropic" not in source
        and "openai" not in source
        and "llm"
        not in source.replace("a language model is never", "").replace("language model", "")
    )
    put_revenue(db, business, [1000] * 12)
    assert make(api, business).status_code == 200


# --- more cases found by trying to break it ----------------------------------------------------


def _line_through(values, steps=1):
    """An independent straight-line fit, to check the method against."""
    n = len(values)
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values)) / sum(
        (x - mean_x) ** 2 for x in range(n)
    )
    return [mean_y + slope * (n - 1 + k - mean_x) for k in range(1, steps + 1)]


def test_the_trend_is_fitted_to_exactly_the_last_twelve_months():
    kinked = [100.0 * k for k in range(1, 9)] + [800.0] * 4  # climbs, then levels off
    history = [5000.0] * 4 + kinked
    assert LinearTrend().predict(history, 2) == pytest.approx(_line_through(kinked, 2))
    assert LinearTrend().predict(history, 2) != pytest.approx(_line_through(kinked[-6:], 2))
    assert LinearTrend().predict(history, 2) != pytest.approx(_line_through(history[-13:], 2))


def test_a_month_marked_ok_but_with_no_figure_breaks_the_run(api, db, business):
    series = Values(db, business)
    for month, value in zip(
        months_back(9), [9, 9, 9, 1000, 1000, 1000, 1000, 1000, 1000], strict=True
    ):
        series.put("revenue", month, None if month == date(2026, 1, 1) else value, None)
    assert make(api, business).json()["history_months"] == 8


def test_more_than_twelve_months_ahead_is_refused_by_the_service_itself(db, business):
    put_revenue(db, business, [1000] * 12)
    with scoped(db, business), pytest.raises(forecast.AppError) as refused:
        forecast.calculate(db, owner_tenant(db, business), "revenue", 13)
    assert refused.value.code == "bad_horizon"
    with scoped(db, business):
        assert forecast.calculate(db, owner_tenant(db, business), "revenue", 12).horizon == 12


def test_whether_a_figure_follows_the_trading_year_is_a_rule_in_the_data(api, db, business):
    from app.models.health import HealthRule

    put_revenue(db, business, [1000] * 11 + [1500])
    with scoped(db, business):
        db.add(season("September", (9, 1), (9, 30), 50, source="user", status="active"))
        db.flush()
    db.execute(
        HealthRule.__table__.update().where(HealthRule.kpi_code == "revenue").values(seasonal=False)
    )
    body = make(api, business).json()
    assert body["adjusted_for_seasons"] is False
    assert body["predictions"][0]["value"] == "1166.67"  # September's 1,500 counts in full


def test_a_season_in_the_months_it_learns_from_is_taken_out_before_it_looks_for_a_pattern(
    api, db, business
):
    put_revenue(db, business, [1000] * 11 + [1500])  # September 2026 was 50% up on a normal month
    with scoped(db, business):
        db.add(season("September", (9, 1), (9, 30), 50, source="user", status="active"))
        db.flush()
    body = make(api, business).json()
    assert body["adjusted_for_seasons"] is True
    assert [p["value"] for p in body["predictions"]] == ["1000.00", "1000.00", "1000.00"]


def test_only_the_last_two_years_of_history_are_returned_with_a_forecast(api, db, business):
    put_revenue(db, business, [1000 + 10 * i for i in range(30)])
    body = make(api, business).json()
    assert body["history_months"] == 30 and len(body["history"]) == 24
    assert body["history"][0]["period_start"] == "2024-10-01"
    assert body["history"][-1]["period_start"] == "2026-09-01"


def test_forecasting_everything_skips_a_business_with_nothing_to_learn_from(db, business):
    with scoped(db, business):
        result = forecast.calculate_all(db, owner_tenant(db, business))
    assert (result.forecasts, result.forecast_months) == (0, 0)
    put_revenue(db, business, [1000] * 12)
    with scoped(db, business):
        result = forecast.calculate_all(db, owner_tenant(db, business))
    assert (result.forecasts, result.forecast_months) == (1, 3)
