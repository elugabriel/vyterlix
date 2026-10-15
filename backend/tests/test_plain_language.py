# ruff: noqa: E501, F811
"""Saying what a figure means, in everyday words (client feedback 15/10/2026, section 2)."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.kpi import KpiDefinition
from app.plain.figures import BEFORE, FLAT_PCT, FLAT_POINTS, VALUE_FIGURES, WORDS, explain_figure
from tests.test_dashboard import dash
from tests.test_kpi_engine import history, run, shop  # noqa: F401


def say(
    code,
    change,
    *,
    unit="gbp",
    direction="up_good",
    value="10.00",
    quality=None,
    granularity="month",
):
    return explain_figure(
        code,
        unit=unit,
        direction=direction,
        value=value,
        change_pct=change,
        data_quality=quality,
        granularity=granularity,
    )


# --- the client's own examples ---------------------------------------------------------------------


def test_stock_turnover_rising_says_what_the_client_asked_for():
    plain = say("stock_turnover", "16.0", unit="ratio")
    assert plain.says == "Your products are selling faster than last month."
    assert plain.why == (
        "That is usually positive because less money is sitting in unsold stock. "
        "Check that your popular products do not run out."
    )
    assert plain.tone == "good"


def test_customers_not_coming_back_says_it_could_reduce_next_months_sales():
    plain = say("customer_churn_pct", "5.0", unit="percent", direction="down_good")
    assert plain.says == "More customers stopped coming back than last month."
    assert "reduce next month's sales" in plain.why
    assert plain.tone == "bad"  # a rise in a "lower is better" figure


# --- every figure is covered, both ways --------------------------------------------------------------


def test_every_figure_in_the_system_has_words_for_both_directions(db):
    codes = set(db.scalars(select(KpiDefinition.code).where(KpiDefinition.is_active.is_(True))))
    assert codes and codes <= set(WORDS), f"no words for: {sorted(codes - set(WORDS))}"
    assert set(WORDS) <= codes, (
        f"words for a figure that does not exist: {sorted(set(WORDS) - codes)}"
    )


@pytest.mark.parametrize("code", sorted(WORDS))
def test_each_figure_has_a_short_sentence_and_a_reason_that_end_properly(code):
    for say_ in (WORDS[code].up, WORDS[code].down):
        for text in (say_.says, say_.why):
            assert text and text[0].isupper() and text.endswith("."), (code, text)
        assert len(say_.says) < 110, (code, say_.says)
        assert len(say_.why) < 220, (code, say_.why)


@pytest.mark.parametrize("code", sorted(WORDS))
def test_the_words_never_state_a_cause(code):
    """A figure alone cannot know why it moved; causes live on the What changed page, with evidence."""
    causal = re.compile(
        r"\b(because of|caused by|due to|as a result of|thanks to|owing to|result of your)\b", re.I
    )
    for say_ in (WORDS[code].up, WORDS[code].down):
        assert not causal.search(say_.says + " " + say_.why), (code, say_)


def test_the_two_comparison_figures_are_judged_by_their_own_sign_not_their_movement():
    assert VALUE_FIGURES == {"revenue_growth_pct", "revenue_vs_last_year_pct"}
    grew = say("revenue_growth_pct", "-3.0", unit="percent", value="4.2")  # growing, if more slowly
    shrank = say("revenue_growth_pct", "2.0", unit="percent", value="-1.5")
    assert (grew.says, grew.tone) == ("Your sales grew compared with last month.", "good")
    assert (shrank.says, shrank.tone) == ("Your sales shrank compared with last month.", "bad")
    flat = say("revenue_growth_pct", None, unit="percent", value="0.00")
    assert flat.tone == "neutral"
    assert say("revenue_vs_last_year_pct", None, unit="percent", value="12.0").tone == "good"
    assert say("revenue_vs_last_year_pct", None, unit="percent", value="-4.0").tone == "bad"
    assert (
        "same time last year"
        in say("revenue_vs_last_year_pct", None, unit="percent", value="-4.0").says
    )


# --- good, bad, watch -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "change", "tone"),
    [
        ("up_good", "5.0", "good"),
        ("up_good", "-5.0", "bad"),
        ("down_good", "5.0", "bad"),
        ("down_good", "-5.0", "good"),
    ],
)
def test_the_tone_follows_whether_up_or_down_is_the_good_way(direction, change, tone):
    assert say("revenue", change, direction=direction).tone == tone


def test_a_figure_that_is_neither_good_nor_bad_by_itself_says_to_watch_it():
    up = say("stock_units", "10.0", unit="count", direction="neutral")
    down = say("stock_units", "-10.0", unit="count", direction="neutral")
    assert up.tone == down.tone == "watch"
    assert "cash is not tied up" in up.why and "popular items do not run out" in down.why


def test_the_share_of_sales_that_name_a_customer_is_good_when_it_rises_and_watched_when_it_falls():
    assert (
        say("customers_identified_pct", "4.0", unit="percent", direction="neutral").tone == "good"
    )
    assert (
        say("customers_identified_pct", "-4.0", unit="percent", direction="neutral").tone == "watch"
    )


def test_takings_with_vat_is_said_to_be_the_wrong_guide():
    plain = say("gross_sales", "8.0", direction="up_good")
    assert plain.tone == "neutral" and "VAT is not yours to keep" in plain.why


# --- when it cannot say, it says so -------------------------------------------------------------------


def test_with_nothing_to_compare_it_says_so_and_does_not_guess():
    plain = say("revenue", None)
    assert plain.tone == "unknown"
    assert (
        plain.says
        == "This is the first period we can show, so there is nothing to compare it with yet."
    )
    assert "one more period" in plain.why


def test_a_tiny_movement_is_called_about_the_same():
    assert say("revenue", "0.99").says == "This is about the same as last month."
    assert say("revenue", str(FLAT_PCT)).says != "This is about the same as last month."
    assert say("revenue", "-0.99").tone == "neutral"
    # a figure that is itself a percentage moves in points, so half a point is the line
    assert say("gross_margin_pct", "0.49", unit="percent").tone == "neutral"
    assert say("gross_margin_pct", str(FLAT_POINTS), unit="percent").tone == "good"


def test_no_number_means_no_words():
    assert (
        explain_figure("revenue", unit="gbp", direction="up_good", value=None, change_pct="5.0")
        is None
    )
    assert (
        explain_figure(
            "not_a_figure", unit="gbp", direction="up_good", value="1.00", change_pct="5.0"
        )
        is None
    )
    assert (
        explain_figure("revenue", unit="gbp", direction="up_good", value="oops", change_pct="5.0")
        is None
    )


def test_incomplete_data_adds_a_caution_and_complete_data_does_not():
    assert (
        say("revenue", "5.0", quality=69).caution
        == "Some of the data behind this figure is incomplete, so treat it with care."
    )
    assert say("revenue", "5.0", quality=70).caution is None
    assert say("revenue", "5.0", quality=None).caution is None
    assert say("revenue", None, quality=10).caution is not None  # even with nothing to compare


def test_the_period_before_is_named_for_the_way_the_figures_are_grouped():
    assert BEFORE == {
        "week": "last week",
        "month": "last month",
        "quarter": "last quarter",
        "year": "last year",
    }
    assert say("revenue", "5.0", granularity="week").says == "You sold more than last week."
    assert (
        say("customer_retention_pct", "5.0", unit="percent", granularity="quarter").says
        == "More of last quarter's customers bought from you again."
    )
    assert (
        say("revenue", "5.0", granularity="decade").says == "You sold more than the period before."
    )


def test_the_words_do_not_depend_on_how_large_the_number_is():
    assert say("revenue", "1.0").says == say("revenue", "900.0").says
    assert Decimal(str(FLAT_PCT)) == Decimal("1")


def test_when_the_earlier_period_was_nothing_it_still_says_which_way_it_went():
    # a percentage of nothing cannot be worked out, but the figure plainly moved
    up = explain_figure(
        "stock_purchases",
        unit="gbp",
        direction="neutral",
        value="50.00",
        change_pct=None,
        previous_value="0.00",
    )
    assert up.says == "You spent more on buying stock than last month." and up.tone == "watch"
    same = explain_figure(
        "stock_purchases",
        unit="gbp",
        direction="neutral",
        value="0.00",
        change_pct=None,
        previous_value="0.00",
    )
    assert same.says == "This is about the same as last month." and same.tone == "neutral"
    down = explain_figure(
        "revenue",
        unit="gbp",
        direction="up_good",
        value="0.00",
        change_pct=None,
        previous_value="10.00",
    )
    assert down.says == "You sold less than last month." and down.tone == "bad"
    # with no earlier period at all, it still says it cannot compare
    first = explain_figure(
        "revenue",
        unit="gbp",
        direction="up_good",
        value="5.00",
        change_pct=None,
        previous_value=None,
    )
    assert first.tone == "unknown"


def test_a_loss_is_called_a_loss_whichever_way_the_figure_moved():
    worse = say("net_profit", "-14.9", value="-4341.53")
    better = say("net_profit", "20.0", value="-100.00")
    assert worse.says == better.says == "Your business made a loss this month."
    assert worse.tone == better.tone == "bad"
    assert "costs were higher than your sales" in worse.why
    assert (
        say("net_margin_pct", "-79.5", unit="percent", value="-245.8").says
        == "You spent more than you took in this month."
    )
    assert (
        say("gross_profit", "-3.0", value="-5.00", granularity="week").says
        == "The goods you sold cost you more than you sold them for this week."
    )
    # a profit above zero is judged by how it moved, as before
    assert (
        say("net_profit", "-14.9", value="100.00").says
        == "Your business made less profit than last month."
    )


# --- what the screens are given -------------------------------------------------------------------------


def test_the_key_figures_list_carries_the_words_for_the_latest_month(api, db, shop):
    run(db, shop, first=__import__("datetime").date(2026, 1, 1))
    body = api.get(f"/api/v1/organizations/{shop[0]}/kpis", headers=shop[2]["owner"]).json()
    sales = next(k for k in body["kpis"] if k["code"] == "revenue")
    assert sales["plain"]["tone"] in {"good", "bad", "neutral", "unknown"}
    assert sales["plain"]["says"] and sales["plain"]["why"]
    nothing = [k for k in body["kpis"] if k["latest"] is None or k["latest"]["status"] != "ok"]
    assert all(k["plain"] is None for k in nothing)


def test_one_figure_s_history_carries_the_words_for_its_newest_finished_month(api, db, shop):
    run(db, shop, first=__import__("datetime").date(2026, 1, 1))
    body = history(api, shop, "revenue").json()
    assert body["plain"] is not None and body["plain"]["says"]


def test_the_front_screen_figures_carry_the_words(api, march):
    sales = next(f for f in dash(api, march)["figures"] if f["code"] == "revenue")
    assert sales["plain"]["says"] == "You sold less than last month."
    assert sales["plain"]["tone"] == "bad"
