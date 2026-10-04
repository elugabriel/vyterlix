"""Driver identification: the arithmetic (pure), then the questions on shops worked out by hand."""

from datetime import date
from decimal import Decimal

import pytest

from app.diagnostics import drivers as rules
from app.models.data import Product
from tests.shops import _expense, _list_item, _make, _sale
from tests.test_health import ORGS

D = Decimal


# --- the arithmetic -------------------------------------------------------------------------


def test_the_change_in_a_count_is_valued_at_the_old_rate_and_the_rate_gets_the_rest():
    # 3 sales of 200 became 4 sales of 102.50: the count's +1 at 200, the rate's -390
    count, rate = rules.two_factors(D(3), D(4), D(200), D("102.5"))
    assert (count, rate) == (200, -390)
    assert count + rate == D(4) * D("102.5") - D(3) * D(200)  # -190, nothing lost


@pytest.mark.parametrize(
    ("n0", "n1", "r0", "r1"),
    [(3, 4, 200, "102.5"), (28, 31, "21.428571", "13.225806"), (10, 4, "7.33", "9.1"),
     (5, 5, 10, 12), (5, 9, 10, 10), (1, 1000, "0.01", "99999.99"), (7, 0, 12, 0), (0, 6, 0, 8)],
)  # fmt: skip
def test_the_two_effects_always_add_up_to_the_whole_change(n0, n1, r0, r1):
    n0, n1, r0, r1 = D(n0), D(n1), D(r0), D(r1)
    count, rate = rules.two_factors(n0, n1, r0, r1)
    assert count + rate == n1 * r1 - n0 * r0


def test_a_count_of_nothing_before_means_it_is_all_the_counts_doing():
    assert rules.two_factors(D(0), D(4), D(0), D(50)) == (200, 0)


def test_when_only_the_rate_moves_the_count_has_no_effect():
    assert rules.two_factors(D(5), D(5), D(10), D(12)) == (0, 10)


def test_when_only_the_count_moves_the_rate_has_no_effect():
    assert rules.two_factors(D(5), D(9), D(10), D(10)) == (40, 0)


def test_price_and_volume_per_product():
    # Sourdough: 130 loaves for 400 (£3.0769 each) became 40 for 130; Cake sold the same;
    # Brownie is new. Sourdough: volume (40-130) x 400/130 = -276.92, price the remaining +6.92.
    split = rules.price_volume(
        {
            "sourdough": (D(130), D(400), D(40), D(130)),
            "cake": (D(20), D(200), D(20), D(200)),
            "brownie": (D(0), D(0), D(10), D(80)),
        }
    )
    assert split.volume.quantize(D("0.01")) == D("-196.92")  # -276.92 and the new 80
    assert split.price.quantize(D("0.01")) == D("6.92")
    assert split.volume + split.price == D("-190")
    parts = {key: (v.quantize(D("0.01")), p.quantize(D("0.01"))) for key, v, p in split.parts}
    assert parts == {
        "sourdough": (D("-276.92"), D("6.92")),
        "cake": (D("0.00"), D("0.00")),
        "brownie": (D("80.00"), D("0.00")),
    }


def test_a_product_that_stopped_selling_is_all_volume():
    split = rules.price_volume({"gone": (D(10), D(100), D(0), D(0))})
    assert (split.volume, split.price) == (-100, 0)


def test_a_discount_shows_up_as_price_not_volume():
    # The same 10 items, but customers paid 80 instead of 100
    split = rules.price_volume({"x": (D(10), D(100), D(10), D(80))})
    assert (split.volume, split.price) == (0, -20)


def test_selling_more_at_a_higher_price_splits_cleanly():
    # 10 for 100 became 20 for 300 (£15 each): volume 10 x £10 = 100, price 20 x £5 = 100
    split = rules.price_volume({"x": (D(10), D(100), D(20), D(300))})
    assert (split.volume, split.price) == (100, 100)


def test_refunded_items_count_back_in_the_volume():
    split = rules.price_volume({"x": (D(10), D(100), D(4), D(40))})  # 6 returned at the same price
    assert (split.volume, split.price) == (-60, 0)


def test_a_share_of_the_change():
    assert rules.share_of(D(200), D(-190)).quantize(D("0.1")) == D("-105.3")
    assert rules.share_of(D(5), D(0)) is None


def test_the_days_in_a_month():
    assert rules.days_in(date(2026, 2, 1), date(2026, 2, 28)) == 28
    assert rules.days_in(date(2028, 2, 1), date(2028, 2, 29)) == 29
    assert rules.days_in(date(2026, 3, 1), date(2026, 3, 31)) == 31


# --- a shop worked out by hand --------------------------------------------------------------


def drivers(api, business, metric, query="", who="owner", org=0):
    res = api.get(
        f"{ORGS}/{business[org]}/drivers/{metric}?month=2026-03-01{query}", headers=business[2][who]
    )
    assert res.status_code == 200, res.text
    return res.json()


def lens(body, key):
    return next(item for item in body["lenses"] if item["key"] == key)


def effect(body, key, kind):
    return next(e for e in lens(body, key)["effects"] if e["kind"] == kind)


def test_the_headline_and_totals(api, bakery):
    body = drivers(api, bakery, "revenue")
    assert (body["total_previous"], body["total_current"], body["total_change"]) == (
        "600.00", "410.00", "-190.00",
    )  # fmt: skip
    assert body["total_change_pct"] == "-31.7"
    assert body["headline"] == (
        "Sales fell by £190.00 (32%) in March 2026 compared with February 2026."
    )
    assert [item["key"] for item in body["lenses"]] == ["days", "orders", "price_volume"]


def test_a_longer_month_adds_sales_all_on_its_own(api, bakery):
    days = effect(drivers(api, bakery, "revenue"), "days", "calendar_days")
    # March has 31 days to February's 28; a day of February came to 600 / 28 = 21.43
    assert (days["amount"], days["share_pct"]) == ("64.29", "-33.8")
    assert (
        days["text"]
        == "March 2026 has 3 more days than February 2026, which on its own adds £64.29."
    )


def test_the_busier_or_slower_days_are_whatever_the_days_do_not_explain(api, bakery):
    body = drivers(api, bakery, "revenue")
    rate = effect(body, "days", "daily_rate")
    assert (rate["amount"], rate["share_pct"]) == ("-254.29", "133.8")
    assert rate["text"] == (
        "On an average day it came to £13.23 against £21.43, which takes off £254.29."
    )
    total = sum(D(e["amount"]) for e in lens(body, "days")["effects"])
    assert total == D("-190.00")


def test_more_sales_but_a_much_smaller_average_sale(api, bakery):
    body = drivers(api, bakery, "revenue")
    count, value = (effect(body, "orders", k) for k in ("sales_count", "sale_value"))
    # 3 sales became 4 (refunds are not sales), and the average sale fell from £200 to £102.50
    assert (count["amount"], count["share_pct"]) == ("200.00", "-105.3")
    assert count["text"] == (
        "1 more sales (4 against 3), valued at the average sale in February 2026 of £200.00, "
        "adds £200.00."
    )
    assert (value["amount"], value["share_pct"]) == ("-390.00", "205.3")
    assert value["text"] == ("A smaller average sale (£102.50 against £200.00) takes off £390.00.")
    assert D(count["amount"]) + D(value["amount"]) == D("-190.00")


def test_fewer_items_sold_at_slightly_better_prices(api, bakery):
    body = drivers(api, bakery, "revenue")
    volume, price = (effect(body, "price_volume", k) for k in ("volume", "price"))
    assert (volume["amount"], volume["share_pct"]) == ("-196.92", "103.6")
    assert volume["text"] == (
        "Selling 80 fewer items (70 against 150), at the prices of February 2026, "
        "takes off £196.92."
    )
    assert (price["amount"], price["share_pct"]) == ("6.92", "-3.6")
    assert price["text"].startswith("Higher prices") and price["text"].endswith("adds £6.92.")
    effects = lens(body, "price_volume")["effects"]
    assert sum(D(e["amount"]) for e in effects) == D("-190.00")  # nothing is lost


def test_every_lens_adds_up_to_the_whole_change(api, bakery):
    for metric in ("revenue", "sales_count", "units_sold", "gross_profit"):
        body = drivers(api, bakery, metric)
        for item in body["lenses"]:
            total = sum(D(e["amount"]) for e in item["effects"])
            assert total == D(body["total_change"]), (metric, item["key"])


def test_a_single_part_that_accounts_for_much_of_the_change_is_a_driver_too(api, bakery):
    body = drivers(api, bakery, "revenue")
    labels = {f["label"]: f for f in body["findings"] if f["kind"] == "contributor"}
    assert labels["Product: Sourdough"]["share_pct"] == "142.1"
    assert labels["Sales channel: Shop"]["share_pct"] == "115.8"
    assert labels["Customer: Jo"]["share_pct"] == "142.1"
    assert labels["Day of the week: Tuesday"]["share_pct"] == "105.3"
    assert labels["Product: Sourdough"]["text"].startswith("Sourdough fell from £400.00")
    assert labels["Product: Sourdough"]["lens"] == "parts"


def test_the_findings_are_the_strongest_first_and_limited(api, bakery):
    findings = drivers(api, bakery, "revenue")["findings"]
    assert len(findings) == 6
    strengths = [abs(D(f["share_pct"])) for f in findings]
    assert strengths == sorted(strengths, reverse=True)
    assert findings[0]["kind"] == "sale_value"  # 205% of the change: the biggest single reading


def test_a_part_that_barely_matters_is_not_called_a_driver(api, db, bakery):
    # Three more tiny sales on top of -190 do not make any one part a driver (all under 25%)
    cake = _make(db, bakery, Product, name="Tea")
    for day in (4, 5, 6):
        _sale(db, bakery, date(2026, 3, day), 1, line=(cake, 1, 0))
    body = drivers(api, bakery, "revenue")
    assert "Product: Tea" not in {f["label"] for f in body["findings"]}


def test_the_number_of_sales_and_items_are_explained_by_the_days_and_the_parts(api, bakery):
    body = drivers(api, bakery, "sales_count")
    assert [item["key"] for item in body["lenses"]] == ["days"]  # no orders or price lens
    # 3 sales in 28 days became 4 in 31: the extra days alone are worth 3 x 3/28 = 0.32
    assert effect(body, "days", "calendar_days")["amount"] == "0.32"
    assert effect(body, "days", "calendar_days")["text"].endswith("adds 0.32.")
    units = drivers(api, bakery, "units_sold")
    assert (units["total_previous"], units["total_current"]) == ("150", "70")
    assert effect(units, "days", "calendar_days")["amount"] == "16.07"  # 3 x 150 / 28


def test_gross_profit_is_read_through_days_and_products(api, bakery):
    body = drivers(api, bakery, "gross_profit")
    assert (body["total_previous"], body["total_current"]) == ("300.00", "215.00")
    assert [item["key"] for item in body["lenses"]] == ["days"]
    assert any(f["label"] == "Product: Sourdough" for f in body["findings"])


def test_running_costs_have_no_lenses_but_their_parts_are_named(api, costs):
    body = drivers(api, costs, "operating_expenses")
    assert body["lenses"] == []
    assert [(f["label"], f["share_pct"]) for f in body["findings"]] == [
        ("Supplier: No supplier", "100.0"),
        ("Cost category: Utilities", "61.5"),
    ]


def test_against_the_same_month_last_year_there_is_no_calendar_effect(api, bakery):
    body = drivers(api, bakery, "revenue", "&against=last_year")
    assert body["compared_with"] == "2025-03-01" and body["total_change"] == "-90.00"
    assert [item["key"] for item in body["lenses"]] == ["orders", "price_volume"]
    count, value = (effect(body, "orders", k) for k in ("sales_count", "sale_value"))
    # 1 sale of 500 became 4 sales of 102.50: +3 at 500 = 1,500, and the average sale -1,590
    assert (count["amount"], value["amount"]) == ("1500.00", "-1590.00")
    volume, price = (effect(body, "price_volume", k) for k in ("volume", "price"))
    # Sourdough: 50 for 500 (£10 each) became 40 for 130: -100 volume, -270 price; Cake and
    # Brownie are new, so +200 and +80 volume
    assert (volume["amount"], price["amount"]) == ("180.00", "-270.00")


def test_a_month_with_nothing_before_it_is_all_the_number_of_sales(api, db, business):
    _sale(db, business, date(2026, 3, 3), 100)
    _sale(db, business, date(2026, 3, 4), 150)
    body = drivers(api, business, "revenue")
    count = effect(body, "orders", "sales_count")
    assert count["amount"] == "250.00"
    assert count["text"] == (
        "There were 2 sales in March 2026 and none in February 2026, which adds £250.00."
    )
    assert effect(body, "orders", "sale_value")["amount"] == "0.00"
    assert body["total_change_pct"] is None
    assert "sale_value" not in {
        f["kind"] for f in body["findings"]
    }  # zero effects are not findings


def test_sales_with_no_product_detail_are_named_not_hidden(api, db, business):
    cake = _make(db, business, Product, name="Cake")
    _sale(db, business, date(2026, 2, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 2, 4), 100)  # no lines
    _sale(db, business, date(2026, 3, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 3, 4), 30)  # no lines
    body = drivers(api, business, "revenue")
    detail = effect(body, "price_volume", "no_product_detail")
    assert (detail["amount"], detail["share_pct"]) == ("-70.00", "100.0")
    effects = lens(body, "price_volume")["effects"]
    assert sum(D(e["amount"]) for e in effects) == D("-70.00")


def test_when_nothing_changed_there_is_nothing_to_take_a_share_of(api, db, business):
    _sale(db, business, date(2026, 2, 3), 100)
    _sale(db, business, date(2026, 3, 3), 100)
    body = drivers(api, business, "revenue")
    assert body["headline"] == "Sales in March 2026 was the same as in February 2026."
    assert all(e["share_pct"] is None for item in body["lenses"] for e in item["effects"])
    assert all(f["share_pct"] is None for f in body["findings"])


def test_a_business_with_no_records_has_nothing_to_explain(api, business):
    body = drivers(api, business, "revenue")
    assert body["total_change"] == "0.00" and body["findings"] == []


def test_a_figure_that_cannot_be_explained_yet_is_refused(api, bakery):
    base = f"{ORGS}/{bakery[0]}/drivers"
    headers = bakery[2]["owner"]
    res = api.get(f"{base}/net_margin_pct?month=2026-03-01", headers=headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "bad_metric"
    assert api.get(f"{base}/revenue", headers=headers).status_code == 422  # no month
    assert (
        api.get(f"{base}/revenue?month=2026-03-01&against=never", headers=headers).status_code
        == 422
    )


def test_any_day_in_the_month_will_do(api, bakery):
    res = api.get(
        f"{ORGS}/{bakery[0]}/drivers/revenue?month=2026-03-19", headers=bakery[2]["owner"]
    )
    assert res.json()["month"] == "2026-03-01"


def test_each_business_sees_only_its_own_records(api, bakery):
    rival = drivers(api, bakery, "revenue", who="other", org=1)
    assert rival["total_current"] == "0.00" and rival["findings"] == []
    res = api.get(
        f"{ORGS}/{bakery[1]}/drivers/revenue?month=2026-03-01", headers=bakery[2]["owner"]
    )
    assert res.status_code == 404


def test_viewers_can_look_and_strangers_cannot(api, bakery):
    assert drivers(api, bakery, "revenue", who="viewer")["findings"]
    assert api.get(f"{ORGS}/{bakery[0]}/drivers/revenue?month=2026-03-01").status_code == 401


def _costs(db, business, rows):
    """Running costs by category: rows of (name, February, March), one expense each month."""
    for name, then, now in rows:
        category = _list_item(db, business, "cost_category", name, is_cost_of_sales=False)
        _expense(db, business, date(2026, 2, 3), then, category=category)
        _expense(db, business, date(2026, 3, 3), now, category=category)


def _categories_named(body):
    return [f["label"] for f in body["findings"] if f["label"].startswith("Cost category:")]


def test_a_part_with_exactly_a_quarter_of_the_change_is_a_driver(api, db, business):
    _costs(db, business, [(n, 100, 125) for n in "ABCD"])  # four parts, each 25% of +100
    body = drivers(api, business, "operating_expenses")
    assert _categories_named(body) == ["Cost category: A"]  # the biggest mover (tied, by name)
    assert (
        next(f for f in body["findings"] if f["label"] == "Cost category: A")["share_pct"] == "25.0"
    )


def test_a_part_with_less_than_a_quarter_of_the_change_is_not(api, db, business):
    _costs(db, business, [(n, 100, 120) for n in "ABCDE"])  # five parts, each 20% of +100
    assert _categories_named(drivers(api, business, "operating_expenses")) == []


def test_a_part_that_worked_against_the_change_is_a_driver_too(api, db, business):
    _costs(db, business, [("A", 200, 100), ("B", 100, 160), ("C", 100, 160)])
    body = drivers(api, business, "operating_expenses")  # +20 overall, but A fell by 100
    found = next(f for f in body["findings"] if f["label"] == "Cost category: A")
    assert found["share_pct"] == "-500.0"
    assert found["text"].endswith("went against the overall change.")


def test_a_month_of_nothing_but_refunds_has_no_average_sale(api, db, business):
    _sale(db, business, date(2026, 2, 3), 100)
    _sale(db, business, date(2026, 3, 3), 20, kind="refund")
    body = drivers(api, business, "revenue")
    value = effect(body, "orders", "sale_value")
    # The one sale of £100 is gone (-100), and the refund makes the rest of the -120 change
    assert effect(body, "orders", "sales_count")["amount"] == "-100.00"
    assert value["amount"] == "-20.00"
    assert value["text"] == "A smaller average sale (£0.00 against £100.00) takes off £20.00."
