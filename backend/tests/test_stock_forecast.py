"""What to stock: the instruction rules (pure), then products worked out by hand."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.forecast import stock as rules
from app.models.business import BusinessSeason
from app.models.data import Product, StockMovement
from app.services import stock_forecast
from tests.shops import _make, _sale
from tests.test_health import ORGS, scoped

D = Decimal
TODAY = date(2026, 10, 4)  # so the last finished month is September 2026


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(stock_forecast, "today_uk", lambda: TODAY)


# --- the instructions -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("on_hand", "state"),
    [(0, "order_now"), (99, "order_now"), (100, "watch"), (102, "watch"), (103, "ok"), (500, "ok")],
)
def test_the_instruction_follows_expected_sales_and_a_busy_month(on_hand, state):
    assert rules.status(expected=100, upper=103, on_hand=on_hand) == state


def test_with_nothing_expected_to_sell_any_stock_is_enough():
    assert rules.status(0, 0, 0) == "ok"
    assert rules.status(0, 5, 0) == "watch"


def test_how_many_to_order_covers_a_busy_month_in_whole_items():
    assert rules.order_quantity(103, 50) == 53
    assert rules.order_quantity(102.2, 50) == 53  # rounded up: 52.2 -> 53
    assert rules.order_quantity(103, 103) == 0
    assert rules.order_quantity(103, 500) == 0  # never negative


def test_days_of_cover_is_the_stock_over_the_daily_pace():
    assert rules.days_of_cover(expected=304, on_hand=304) == pytest.approx(30.4)
    assert rules.days_of_cover(expected=100, on_hand=50) == pytest.approx(15.2)
    assert rules.days_of_cover(expected=100, on_hand=0) == 0.0
    assert rules.days_of_cover(expected=100, on_hand=-20) == 0.0  # a negative count means none
    assert rules.days_of_cover(expected=0, on_hand=50) is None


def test_the_most_urgent_comes_first():
    assert [k for k, _ in sorted(rules.URGENCY.items(), key=lambda kv: kv[1])] == [
        "order_now", "watch", "ok",
    ]  # fmt: skip


# --- products worked out by hand -------------------------------------------------------------


def months(first=2, last=9):
    return [date(2026, m, 5) for m in range(first, last + 1)]


def sell(db, business, product, per_month, days=None, kind="sale", qty_each=None):
    for day in days or months():
        _sale(db, business, day, 10, line=(product, qty_each or per_month, 0), kind=kind)


def stock(db, business, product, quantity, day=date(2026, 9, 1), kind="delivery"):
    with scoped(db, business):
        db.add(
            StockMovement(
                product_id=product, moved_on=day, kind=kind, quantity=D(quantity), source="manual"
            )
        )
        db.flush()


def need(db, business):
    with scoped(db, business):
        return stock_forecast.requirements(db, TODAY)


def row(result, name):
    return next(r for r in result.rows if r.name == name)


@pytest.fixture
def bakery_stock(db, business):
    """Four products, each selling 100 a month from February to September 2026, with 50, 100, 103
    and none on the shelf. 100 a month with no ups and downs: expected 100, a busy month at most
    102.56 (2% of the level is the least the range is ever allowed to be, x 1.2816)."""
    ids = {}
    for name, shelf in (("Bagel", 50), ("Roll", 100), ("Bun", 103), ("Zero", None)):
        ids[name] = _make(db, business, Product, name=name)
        sell(db, business, ids[name], 100)
        if shelf is not None:
            stock(db, business, ids[name], shelf)
    return ids


def test_each_product_is_told_what_to_do(db, business, bakery_stock):
    result = need(db, business)
    states = {
        r.name: (r.expected_units, r.lower_units, r.upper_units, r.on_hand, r.status)
        for r in result.rows
    }
    assert states == {
        "Bagel": (100, 97, 103, 50, "order_now"),
        "Roll": (100, 97, 103, 100, "watch"),
        "Bun": (100, 97, 103, 103, "ok"),
        "Zero": (100, 97, 103, 0, "order_now"),
    }
    assert (result.month, result.as_of, result.level) == (date(2026, 10, 1), date(2026, 9, 1), 80)


def test_how_many_to_order_and_how_long_the_stock_lasts(db, business, bakery_stock):
    result = need(db, business)
    assert [(r.name, r.order_suggested, r.days_of_cover) for r in result.rows] == [
        ("Zero", 103, 0),  # nothing on the shelf
        ("Bagel", 53, 15),  # 50 / (100 / 30.4) = 15.2 days
        ("Roll", 3, 30),  # covers the expected 100, not a busy month
        ("Bun", 0, 31),  # 103 / (100 / 30.4) = 31.3 days
    ]


def test_the_most_urgent_products_come_first_and_the_shortest_cover_leads_within_a_band(
    db, business, bakery_stock
):
    result = need(db, business)
    assert [r.status for r in result.rows] == ["order_now", "order_now", "watch", "ok"]
    assert [r.name for r in result.rows][:2] == ["Zero", "Bagel"]  # 0 days then 15 days


def test_the_summary_counts_the_products_in_each_state(db, business, bakery_stock):
    result = need(db, business)
    assert (result.order_now, result.watch, result.ok, result.not_enough_history) == (2, 1, 1, 0)
    assert result.headline == "2 products to order now, 1 to watch, 1 well stocked."


def test_a_product_that_is_a_step_short_of_its_expected_sales_is_not_yet_a_worry(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    stock(db, business, item, 99)
    assert row(need(db, business), "Tart").status == "order_now"  # 99 is under the 100 expected


def test_stock_dated_in_the_future_is_not_on_the_shelf_yet(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    stock(db, business, item, 50)
    stock(db, business, item, 500, day=TODAY + timedelta(days=1))
    assert row(need(db, business), "Tart").on_hand == 50
    stock(db, business, item, 30, day=TODAY)  # today counts
    assert row(need(db, business), "Tart").on_hand == 80


def test_waste_and_sales_come_off_what_is_on_the_shelf(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    stock(db, business, item, 300)
    stock(db, business, item, -40, kind="write_off")
    stock(db, business, item, -60, kind="sale")
    assert row(need(db, business), "Tart").on_hand == 200


def test_the_current_month_is_not_learned_from_because_it_is_still_running(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    _sale(db, business, date(2026, 10, 2), 10, line=(item, 5000, 0))  # a freak start to October
    assert row(need(db, business), "Tart").expected_units == 100


def test_refunded_items_count_back_so_a_returned_batch_does_not_look_like_demand(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 120)
    sell(db, business, item, 20, kind="refund")  # 20 a month came back
    assert row(need(db, business), "Tart").expected_units == 100


def test_a_month_with_no_sales_between_sales_is_a_month_of_zero(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(
        db,
        business,
        item,
        100,
        days=[date(2026, 2, 5), date(2026, 3, 5), date(2026, 8, 5), date(2026, 9, 5)],
    )
    result = need(db, business)
    tart = row(result, "Tart")
    assert tart.history_months == 8  # February to September, the quiet months counted as nothing
    assert tart.expected_units < 100  # so it is not forecast to sell as if it never paused


def test_a_product_that_stopped_selling_is_expected_to_sell_none_and_never_runs_short(db, business):
    item = _make(db, business, Product, name="Old line")
    sell(db, business, item, 100, days=[date(2026, m, 5) for m in range(2, 6)])  # February to May
    result = need(db, business)
    old = row(result, "Old line")
    assert old.history_months == 8 and old.expected_units < 60
    stock(db, business, item, 0, kind="opening")
    assert row(need(db, business), "Old line").status in ("order_now", "watch", "ok")


def test_a_product_with_too_few_months_is_set_aside_not_guessed_at(db, business, bakery_stock):
    new = _make(db, business, Product, name="New bake")
    sell(db, business, new, 80, days=[date(2026, m, 5) for m in (7, 8, 9)])
    result = need(db, business)
    assert result.not_enough_history == 1 and all(r.name != "New bake" for r in result.rows)
    assert result.headline == (
        "2 products to order now, 1 to watch, 1 well stocked. "
        "1 product without enough sales history yet."
    )


def test_exactly_six_months_is_enough_and_five_is_not(db, business):
    six = _make(db, business, Product, name="Six")
    five = _make(db, business, Product, name="Five")
    sell(db, business, six, 50, days=[date(2026, m, 5) for m in range(4, 10)])
    sell(db, business, five, 50, days=[date(2026, m, 5) for m in range(5, 10)])
    result = need(db, business)
    assert [r.name for r in result.rows] == ["Six"] and result.not_enough_history == 1


def test_products_that_are_switched_off_are_left_out(db, business):
    gone = _make(db, business, Product, name="Retired", is_active=False)
    sell(db, business, gone, 100)
    result = need(db, business)
    assert result.rows == [] and result.not_enough_history == 0


def test_a_product_never_sold_is_not_forecast(db, business):
    _make(db, business, Product, name="Never sold")
    assert need(db, business).not_enough_history == 1


def test_no_sales_history_says_how_much_is_needed(db, business):
    result = need(db, business)
    assert result.rows == [] and (result.order_now, result.watch, result.ok) == (0, 0, 0)
    assert result.headline == (
        "Not enough sales history yet to say how much to stock: we need at least 6 finished "
        "months of sales for a product."
    )


def test_a_confirmed_busy_season_in_the_coming_month_raises_what_to_expect(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    with scoped(db, business):
        db.add(
            BusinessSeason(
                name="October half term", start_month=10, start_day=1, end_month=10, end_day=31,
                expected_change_pct=D("50"), source="user", status="active",
            )
        )  # fmt: skip
        db.flush()
    tart = row(need(db, business), "Tart")
    assert (tart.expected_units, tart.upper_units) == (150, 154)  # 100 x 1.5, and 102.56 x 1.5


def test_a_suggested_season_the_owner_has_not_confirmed_changes_nothing(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    with scoped(db, business):
        db.add(
            BusinessSeason(
                name="Half term?", start_month=10, start_day=1, end_month=10, end_day=31,
                expected_change_pct=D("50"), source="detected", status="suggested",
            )
        )  # fmt: skip
        db.flush()
    assert row(need(db, business), "Tart").expected_units == 100


def test_a_growing_product_is_expected_to_keep_growing(db, business):
    item = _make(db, business, Product, name="Tart")
    days = [date(2025, 12, 5)] + [date(2026, m, 5) for m in range(1, 10)]  # ten months
    for day, units in zip(days, range(100, 200, 10), strict=True):
        _sale(db, business, day, 10, line=(item, units, 0))
    tart = row(need(db, business), "Tart")
    assert tart.method == "linear_trend" and tart.expected_units == 200  # the line runs on to 200


def test_which_method_was_used_and_how_far_off_it_has_been_is_shown(db, business, bakery_stock):
    bagel = row(need(db, business), "Bagel")
    assert bagel.method == "moving_average" and bagel.history_months == 8
    assert bagel.typical_miss_pct == "0"


# --- through the API --------------------------------------------------------------------------


def test_the_requirements_are_available_to_everyone_in_the_business(
    api, db, business, bakery_stock
):
    for who in ("owner", "viewer"):
        res = api.get(
            f"{ORGS}/{business[0]}/forecasts/stock-requirements", headers=business[2][who]
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert [r["name"] for r in body["rows"]][:2] == ["Zero", "Bagel"]
        assert body["month"] == "2026-10-01" and body["headline"].startswith(
            "2 products to order now"
        )


def test_the_requirements_are_not_mistaken_for_a_figure_to_forecast(api, business):
    res = api.get(
        f"{ORGS}/{business[0]}/forecasts/stock-requirements", headers=business[2]["owner"]
    )
    assert res.status_code == 200 and "rows" in res.json()
    bad = api.get(f"{ORGS}/{business[0]}/forecasts/stock", headers=business[2]["owner"])
    assert bad.status_code == 422


def test_each_business_sees_only_its_own_products(api, db, business, bakery_stock):
    res = api.get(
        f"{ORGS}/{business[1]}/forecasts/stock-requirements", headers=business[2]["other"]
    )
    assert res.status_code == 200 and res.json()["rows"] == []
    assert (
        api.get(
            f"{ORGS}/{business[1]}/forecasts/stock-requirements", headers=business[2]["owner"]
        ).status_code
        == 404
    )
    assert api.get(f"{ORGS}/{business[0]}/forecasts/stock-requirements").status_code == 401


def test_a_busy_product_with_room_to_spare_still_comes_before_a_well_stocked_one(db, business):
    # Slow: a steady 100 a month with 103 on the shelf (31 days): well stocked.
    # Lumpy: 60 and 140 in turn, so a busy month could be much bigger than usual; 175 on the shelf
    # lasts longer (about 46 days) but is not enough for a busy month, so it is the one to watch.
    slow = _make(db, business, Product, name="Slow")
    lumpy = _make(db, business, Product, name="Lumpy")
    sell(db, business, slow, 100)
    for day, units in zip(months(), (60, 140) * 4, strict=True):
        _sale(db, business, day, 10, line=(lumpy, units, 0))
    stock(db, business, slow, 103)
    stock(db, business, lumpy, 175)
    result = need(db, business)
    assert row(result, "Slow").status == "ok" and row(result, "Lumpy").status == "watch"
    assert row(result, "Lumpy").days_of_cover > row(result, "Slow").days_of_cover
    assert [r.name for r in result.rows] == ["Lumpy", "Slow"]


def test_products_in_the_same_state_with_the_same_cover_are_listed_by_name_ignoring_case(
    db, business
):
    for name in ("banana", "Apple", "cherry"):
        item = _make(db, business, Product, name=name)
        sell(db, business, item, 100)
        stock(db, business, item, 50)
    assert [r.name for r in need(db, business).rows] == ["Apple", "banana", "cherry"]


def test_a_busy_season_in_months_it_learns_from_is_taken_out_before_looking_for_a_pattern(
    db, business
):
    item = _make(db, business, Product, name="Tart")
    summer = {6: 150, 7: 150, 8: 150}  # a summer rush, 50% up on a normal month
    for month in range(2, 10):
        _sale(db, business, date(2026, month, 5), 10, line=(item, summer.get(month, 100), 0))
    with scoped(db, business):
        db.add(
            BusinessSeason(
                name="Summer", start_month=6, start_day=1, end_month=8, end_day=31,
                expected_change_pct=D("50"), source="user", status="active",
            )
        )  # fmt: skip
        db.flush()
    tart = row(need(db, business), "Tart")
    assert (
        tart.expected_units == 100
    )  # October is a normal month, and the rush is not mistaken for growth


def test_a_product_selling_less_each_month_is_never_expected_to_sell_less_than_nothing(
    db, business
):
    item = _make(db, business, Product, name="Fading")
    days = [date(2025, 12, 5)] + [
        date(2026, m, 5) for m in range(1, 10)
    ]  # ten months, 90 down to 0
    for day, units in zip(days, range(90, -1, -10), strict=True):
        if units:
            _sale(db, business, day, 10, line=(item, units, 0))
    fading = row(need(db, business), "Fading")
    assert (
        fading.method == "linear_trend" and fading.expected_units == 0 and fading.lower_units == 0
    )


def test_one_product_is_one_product_in_the_summary(db, business):
    item = _make(db, business, Product, name="Tart")
    sell(db, business, item, 100)
    stock(db, business, item, 10)
    assert need(db, business).headline == "1 product to order now, 0 well stocked."
