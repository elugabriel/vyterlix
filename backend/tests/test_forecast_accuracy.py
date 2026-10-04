"""Checking forecasts against what happened: the scoring (pure), then forecasts that come true."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.forecast import accuracy as rules
from app.models.forecast import Forecast, ForecastEvaluation, ForecastPrediction
from app.services import forecast
from tests.test_detection import Values
from tests.test_forecast import _forecast_row, make, months_back, put_revenue
from tests.test_health import ORGS, owner_tenant, scoped

D = Decimal


def approx(value, expected, places=4):
    assert abs(value - expected) < 10**-places, (value, expected)


def item(predicted, lower, upper, actual):
    return rules.Checked(float(predicted), float(lower), float(upper), float(actual))


# Four forecast months, worked out by hand:
#   A: said 1000 (900-1100), was 1050: inside; forecast 4.76% too low
#   B: said 1000 (900-1100), was 800: outside; forecast 25% too high
#   C: said 2000 (1800-2200), was 2000: inside; spot on
#   D: said 500 (450-550), was 560: outside (just); forecast 10.71% too low
FOUR = [
    item(1000, 900, 1100, 1050),
    item(1000, 900, 1100, 800),
    item(2000, 1800, 2200, 2000),
    item(500, 450, 550, 560),
]


# --- the scoring ----------------------------------------------------------------------------


def test_nothing_checked_means_nothing_to_score():
    assert rules.score([]) is None


def test_the_typical_miss_and_how_often_the_range_held():
    result = rules.score(FOUR)
    assert (result.n, result.within) == (4, 2)
    assert result.mae == pytest.approx((50 + 200 + 0 + 60) / 4)  # 77.5
    assert result.rmse == pytest.approx(((2500 + 40000 + 0 + 3600) / 4) ** 0.5)  # 107.36
    approx(result.mape, (50 / 1050 + 200 / 800 + 0 + 60 / 560) / 4 * 100)  # 10.12%
    approx(result.bias_pct, (-50 / 1050 + 200 / 800 + 0 - 60 / 560) / 4 * 100)  # +2.38% too high


def test_the_big_misses_count_for_more_in_the_squared_measure():
    result = rules.score(FOUR)
    assert result.rmse > result.mae


def test_a_forecast_that_leans_one_way_is_seen_even_when_the_misses_are_small():
    always_high = [item(110, 100, 120, 100), item(220, 200, 240, 200), item(55, 50, 60, 50)]
    result = rules.score(always_high)
    approx(result.bias_pct, 10.0)
    assert rules.score([item(100, 90, 110, 110), item(100, 90, 110, 90)]).bias_pct == pytest.approx(
        (-10 / 110 + 10 / 90) / 2 * 100
    )  # up one month, down the next: nearly cancels


def test_the_edges_of_the_range_count_as_inside():
    assert rules.within_range(item(100, 90, 110, 90)) and rules.within_range(
        item(100, 90, 110, 110)
    )
    assert not rules.within_range(item(100, 90, 110, 89.99))
    assert not rules.within_range(item(100, 90, 110, 110.01))


def test_a_month_of_nothing_cannot_be_measured_in_per_cent_but_its_pounds_count():
    result = rules.score([item(100, 90, 110, 0), item(100, 90, 110, 100)])
    assert result.mae == 50 and result.mape == pytest.approx(0.0)  # only the second counts
    only_zero = rules.score([item(100, 90, 110, 0)])
    assert only_zero.mape is None and only_zero.bias_pct is None and only_zero.mae == 100


@pytest.mark.parametrize(
    ("bias", "text"),
    [
        (None, "about right on average, not leaning either way"),
        (0.0, "about right on average, not leaning either way"),
        (1.99, "about right on average, not leaning either way"),
        (-1.99, "about right on average, not leaning either way"),
        (2.0, "2% too high on average"),
        (-2.0, "2% too low on average"),
        (12.6, "13% too high on average"),
        (-33.4, "33% too low on average"),
    ],
)
def test_which_way_the_forecast_leans_in_words(bias, text):
    assert rules.bias_text(bias) == text


def test_the_ranges_are_trusted_when_they_hold_close_to_as_often_as_promised():
    assert rules.verdict(80.0, 80) == "The ranges are doing their job."
    assert rules.verdict(70.0, 80) == "The ranges are doing their job."  # ten points of slack
    assert rules.verdict(69.9, 80).startswith("The ranges are too narrow")
    assert rules.verdict(100.0, 80) == "The ranges are doing their job."
    assert rules.verdict(55.0, 60) == "The ranges are doing their job."


def test_the_summary_says_how_often_the_range_held_and_how_far_off_it_was():
    text, verdict = rules.headline(rules.score(FOUR), "gbp", 80, "sales")
    assert text == (
        "Of 4 forecast months checked, 2 (50%) landed inside the range we gave (we aim for 80%). "
        "On average the forecast was off by £77.50 (10%), and was 2% too high on average."
    )
    assert verdict.startswith("The ranges are too narrow")


def test_it_will_not_pass_judgement_on_fewer_than_three_months():
    for count, words in ((0, "only 0 forecast months have"), (1, "only 1 forecast month has"),
                         (2, "only 2 forecast months have")):  # fmt: skip
        result = rules.score(FOUR[:count])
        text, verdict = rules.headline(result, "gbp", 80, "sales")
        assert verdict is None and words in text and "we need 3" in text
        assert text.startswith("It is too early to judge how accurate the sales forecasts are")
    text, verdict = rules.headline(rules.score(FOUR[:3]), "gbp", 80, "sales")
    assert verdict is not None and text.startswith("Of 3 forecast months checked")


def test_the_summary_leaves_out_the_per_cent_when_every_actual_was_zero():
    zeros = [item(100, 90, 110, 0)] * 3
    text, _ = rules.headline(rules.score(zeros), "gbp", 80, "sales")
    assert "off by £100.00, and was about right" in text


# --- forecasts that come true ---------------------------------------------------------------


def predictions(db, business):
    with scoped(db, business):
        return {
            p.period_start: p
            for p in db.scalars(
                select(ForecastPrediction).order_by(ForecastPrediction.period_start)
            )
        }


def refresh(db, business):
    with scoped(db, business):
        return forecast.refresh_actuals(db, owner_tenant(db, business))


def accuracy(api, business, who="owner", org=0, code="revenue"):
    res = api.get(f"{ORGS}/{business[org]}/forecasts/{code}/accuracy", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def made(api, db, business):
    """A year of steady 1,000s, forecast for October, November and December 2026:
    1,000 each month, with ranges 974.37-1025.63, 963.75-1036.25 and 955.60-1044.40."""
    series = put_revenue(db, business, [1000] * 12)
    make(api, business)
    return series


def test_a_month_that_finishes_fills_in_what_really_happened(db, business, made):
    assert refresh(db, business) == 0  # nothing has finished yet
    assert all(p.actual_value is None for p in predictions(db, business).values())
    made.put("revenue", date(2026, 10, 1), 1010, None)
    assert refresh(db, business) == 1
    october = predictions(db, business)[date(2026, 10, 1)]
    assert october.actual_value == D("1010") and october.actual_recorded_at is not None
    assert predictions(db, business)[date(2026, 11, 1)].actual_value is None


def test_running_it_again_changes_nothing_but_a_corrected_figure_is_picked_up(db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    assert refresh(db, business) == 1 and refresh(db, business) == 0
    with scoped(db, business):
        db.execute(
            ForecastPrediction.__table__.update()
            .where(ForecastPrediction.period_start == date(2026, 10, 1))
            .values(actual_value=D("1")),
        )
    assert refresh(db, business) == 1  # put right again
    from app.models.kpi import KpiValue

    with scoped(db, business):
        db.execute(
            KpiValue.__table__.update()
            .where(KpiValue.period_start == date(2026, 10, 1))
            .values(value=D("1020"))
        )
    assert refresh(db, business) == 1
    assert predictions(db, business)[date(2026, 10, 1)].actual_value == D("1020")


def test_a_month_still_in_progress_or_not_worked_out_is_not_filled_in(db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None, complete=False)
    made.put("revenue", date(2026, 11, 1), None, None, status="undefined")
    assert refresh(db, business) == 0
    assert all(p.actual_value is None for p in predictions(db, business).values())


def test_a_forecast_is_scored_on_the_months_that_have_finished(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    made.put("revenue", date(2026, 11, 1), 1100, None)  # outside its range (up to 1,036.25)
    made.put("revenue", date(2026, 12, 1), 1000, None)
    refresh(db, business)
    body = api.get(f"{ORGS}/{business[0]}/forecasts/revenue", headers=business[2]["owner"]).json()
    scored = [e for e in body["evaluations"] if e["kind"] == "actual"]
    assert len(scored) == 1
    assert (scored[0]["method"]["code"], scored[0]["months_tested"], scored[0]["chosen"]) == (
        "moving_average", 3, True,
    )  # fmt: skip
    assert scored[0]["typical_miss"] == "36.67"  # (10 + 100 + 0) / 3
    assert scored[0]["typical_miss_pct"] == "3.4"
    assert [p["actual_value"] for p in body["predictions"]] == ["1010.00", "1100.00", "1000.00"]
    with scoped(db, business):
        stored = db.scalars(
            select(ForecastEvaluation).where(ForecastEvaluation.method == "actual")
        ).one()
        assert stored.within_range == 2


def test_scoring_again_replaces_the_score_it_does_not_add_another(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    refresh(db, business)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    refresh(db, business)
    with scoped(db, business):
        scores = db.scalars(
            select(ForecastEvaluation).where(ForecastEvaluation.method == "actual")
        ).all()
        assert len(scores) == 1 and scores[0].n_points == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(ForecastEvaluation)
                .where(ForecastEvaluation.method == "backtest")
            )
            == 2
        )


def test_how_accurate_the_forecasts_have_been(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    made.put("revenue", date(2026, 12, 1), 1000, None)
    refresh(db, business)
    body = accuracy(api, business)
    assert (body["enough_data"], body["checked"], body["within_range"]) == (True, 3, 2)
    assert (body["within_range_pct"], body["promised_pct"]) == ("66.7", 80)
    assert (body["typical_miss"], body["typical_miss_pct"], body["bias_pct"]) == (
        "36.67",
        "3.4",
        "-3.4",
    )
    assert body["headline"] == (
        "Of 3 forecast months checked, 2 (67%) landed inside the range we gave (we aim for 80%). "
        "On average the forecast was off by £36.67 (3%), and was 3% too low on average."
    )
    assert body["verdict"].startswith("The ranges are too narrow")


def test_how_the_forecasts_did_at_each_distance_ahead(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    made.put("revenue", date(2026, 12, 1), 1000, None)
    refresh(db, business)
    ahead = accuracy(api, business)["by_months_ahead"]
    assert ahead == [
        {"months_ahead": 1, "checked": 1, "typical_miss_pct": "1.0", "within_range_pct": "100.0"},
        {"months_ahead": 2, "checked": 1, "typical_miss_pct": "9.1", "within_range_pct": "0.0"},
        {"months_ahead": 3, "checked": 1, "typical_miss_pct": "0.0", "within_range_pct": "100.0"},
    ]


def test_each_checked_month_is_listed_newest_first_with_its_miss(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    made.put("revenue", date(2026, 12, 1), 1000, None)
    refresh(db, business)
    rows = accuracy(api, business)["rows"]
    assert [r["period_start"] for r in rows] == ["2026-12-01", "2026-11-01", "2026-10-01"]
    assert rows[1] == {
        "period_start": "2026-11-01", "made_from": "2026-09-01", "months_ahead": 2,
        "predicted": "1000.00", "lower": "963.75", "upper": "1036.25", "actual": "1100.00",
        "error": "-100.00", "error_pct": "-9.1", "within_range": False,
    }  # fmt: skip
    assert rows[2]["within_range"] is True and rows[2]["error"] == "-10.00"
    assert rows[0]["error"] == "0.00" and rows[0]["error_pct"] == "0.0"


def test_a_few_checked_months_are_too_few_to_judge(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    refresh(db, business)
    body = accuracy(api, business)
    assert body["enough_data"] is False and body["checked"] == 2 and body["verdict"] is None
    assert body["headline"].startswith(
        "It is too early to judge how accurate the sales forecasts are"
    )
    assert (
        body["within_range_pct"] == "50.0" and len(body["rows"]) == 2
    )  # still shown, just not judged


def test_nothing_checked_yet(api, business):
    body = accuracy(api, business)
    assert body["enough_data"] is False and body["checked"] == 0 and body["rows"] == []
    assert body["within_range_pct"] is None and body["typical_miss"] is None
    assert body["by_months_ahead"] == [] and body["promised_pct"] == 80


def test_older_forecasts_keep_being_checked_after_a_new_one_is_made(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    refresh(db, business)
    made.put("revenue", date(2026, 11, 1), 1100, None)
    assert (
        make(api, business).status_code == 200
    )  # a new forecast from October, noting November first
    with scoped(db, business):
        assert db.scalar(select(func.count()).select_from(Forecast)) == 2
    rows = accuracy(api, business)["rows"]
    assert [(r["period_start"], r["made_from"]) for r in rows] == [
        ("2026-11-01", "2026-09-01"),
        ("2026-10-01", "2026-09-01"),
    ]  # the first forecast's two months; the new one's months have not finished


def test_forecasting_everything_notes_what_happened_first(db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    with scoped(db, business):
        forecast.calculate_all(db, owner_tenant(db, business))
    assert predictions(db, business)[date(2026, 10, 1)].actual_value == D("1010")


def test_a_forecast_replaced_from_the_same_month_leaves_older_scores_alone(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    refresh(db, business)
    first = make(api, business).json()  # a second forecast, from October
    again = make(api, business, "?horizon=2").json()  # replaces it
    assert again["id"] != first["id"] and again["as_of"] == "2026-10-01"
    assert accuracy(api, business)["checked"] == 1  # September's forecast still counts


def test_viewers_can_see_accuracy_and_each_business_only_its_own(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 1010, None)
    refresh(db, business)
    assert accuracy(api, business, who="viewer")["checked"] == 1
    assert accuracy(api, business, who="other", org=1)["checked"] == 0
    res = api.get(f"{ORGS}/{business[1]}/forecasts/revenue/accuracy", headers=business[2]["owner"])
    assert res.status_code == 404
    assert api.get(f"{ORGS}/{business[0]}/forecasts/revenue/accuracy").status_code == 401
    bad = api.get(
        f"{ORGS}/{business[0]}/forecasts/net_profit/accuracy", headers=business[2]["owner"]
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "bad_metric"


def test_an_actual_must_be_inside_the_number_of_months_it_was_scored_on(db, business):
    forecast_id = _forecast_row(db, business)
    for bad in (-1, 4):
        with pytest.raises(IntegrityError), scoped(db, business):
            db.add(
                ForecastEvaluation(
                    forecast_id=forecast_id, model_code="moving_average", method="actual",
                    mae=D("1"), rmse=D("1"), n_points=3, within_range=bad,
                )
            )  # fmt: skip
            db.flush()
        db.rollback()
        forecast_id = _forecast_row(
            db, business, as_of=date(2026, 8, 1) if bad == -1 else date(2026, 7, 1)
        )


def test_the_months_between_are_counted_for_every_forecast_not_just_the_first(api, db, business):
    series = Values(db, business)
    for month, value in zip(months_back(12, (2026, 8)), [1000] * 12, strict=True):
        series.put("revenue", month, value, None)
    make(api, business)  # from August: September, October, November
    series.put("revenue", date(2026, 9, 1), 1000, None)
    series.put("revenue", date(2026, 10, 1), 1000, None)
    refresh(db, business)
    assert [r["months_ahead"] for r in accuracy(api, business)["rows"]] == [2, 1]


def test_the_promised_range_is_the_level_the_forecasts_were_made_at(api, db, business):
    series = put_revenue(db, business, [1000] * 12)
    make(api, business, "?level=90")
    for month, value in ((10, 1010), (11, 1000), (12, 990)):
        series.put("revenue", date(2026, month, 1), value, None)
    refresh(db, business)
    body = accuracy(api, business)
    assert body["promised_pct"] == 90
    assert "(we aim for 90%)" in body["headline"]


def test_a_month_of_nothing_has_a_miss_in_pounds_but_none_in_per_cent(api, db, business, made):
    made.put("revenue", date(2026, 10, 1), 0, None)
    refresh(db, business)
    [row] = accuracy(api, business)["rows"]
    assert (row["actual"], row["error"], row["error_pct"], row["within_range"]) == (
        "0.00", "1000.00", None, False,
    )  # fmt: skip


def test_only_the_latest_two_years_of_checked_months_are_listed_but_all_are_counted(
    api, db, business
):
    series = put_revenue(db, business, [1000] * 12)
    make(api, business, "?horizon=12")  # from September 2026
    series.put("revenue", date(2026, 10, 1), 1000, None)
    make(api, business, "?horizon=12")  # from October
    series.put("revenue", date(2026, 11, 1), 1000, None)
    make(api, business, "?horizon=12")  # from November
    month, year = 12, 2026  # December 2026 to the end of 2027, all finished
    while (year, month) <= (2027, 12):
        series.put("revenue", date(year, month, 1), 1000, None)
        month, year = (1, year + 1) if month == 12 else (month + 1, year)
    refresh(db, business)
    body = accuracy(api, business)
    assert body["checked"] == 36 and len(body["rows"]) == 24  # three forecasts of twelve months
