"""Segment analysis: the comparing (pure), then the database questions on a shop done by hand."""

from datetime import date
from decimal import Decimal

import pytest

from app.diagnostics import segments as rules
from app.models.data import Customer, Product
from tests.shops import _make, _sale
from tests.test_health import ORGS, scoped

D = Decimal


# --- comparing two periods ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "previous", "state"),
    [
        (5, 0, "new"),
        (0, 5, "gone"),
        (7, 5, "up"),
        (3, 5, "down"),
        (5, 5, "steady"),
        (0, 0, "steady"),
    ],
)
def test_what_a_part_did(current, previous, state):
    assert rules.state_of(D(current), D(previous)) == state


def test_a_share_of_the_change_can_pass_a_hundred_per_cent_or_go_negative():
    assert rules.share_of(D(-270), D(-190)).quantize(D("0.1")) == D("142.1")
    assert rules.share_of(D(80), D(-190)).quantize(D("0.1")) == D("-42.1")
    assert rules.share_of(D(5), D(0)) is None


def test_parts_are_ordered_by_how_far_they_moved_and_unmoved_parts_are_left_out():
    current = {
        "a": ("Apples", D(100)),
        "b": ("Bread", D(300)),
        "c": ("Cake", D(50)),
        "d": ("Dates", D(7)),
    }
    previous = {
        "a": ("Apples", D(100)),
        "b": ("Bread", D(500)),
        "c": ("Cake", D(20)),
        "d": ("Dates", D(7)),
    }
    result = rules.compare(current, previous)
    assert [(r.label, r.change) for r in result.rows] == [("Bread", -200), ("Cake", 30)]
    assert (result.total_current, result.total_previous, result.total_change) == (457, 627, -170)


def test_a_part_that_appears_or_disappears_is_a_row_too():
    result = rules.compare({"n": ("New", D(40))}, {"g": ("Gone", D(90))})
    new, gone = sorted(result.rows, key=lambda r: r.label, reverse=True)
    assert (new.label, new.state, new.previous) == ("New", "new", 0)
    assert (gone.label, gone.state, gone.current) == ("Gone", "gone", 0)
    assert [r.label for r in result.rows] == ["Gone", "New"]  # 90 moved further than 40


def test_ties_are_broken_by_name_so_the_order_never_depends_on_the_data():
    current = {"x": ("Zed", D(10)), "y": ("Amy", D(10))}
    result = rules.compare(current, {})
    assert [r.label for r in result.rows] == ["Amy", "Zed"]


def test_the_parts_beyond_the_limit_are_rolled_into_everything_else():
    current = {str(i): (f"P{i}", D(i * 10)) for i in range(1, 7)}  # 10, 20 ... 60 from nothing
    result = rules.compare(current, {}, limit=3)
    assert [r.label for r in result.rows] == ["P6", "P5", "P4", "Everything else"]
    rest = result.rows[-1]
    assert rest.key is None and rest.current == 60 and rest.previous == 0  # 10 + 20 + 30
    assert rest.share_of_change_pct.quantize(D("0.1")) == D("28.6")  # 60 of 210
    assert sum(r.change for r in result.rows) == result.total_change


def test_exactly_at_the_limit_nothing_is_rolled_up():
    current = {str(i): (f"P{i}", D(i)) for i in range(1, 4)}
    assert [r.label for r in rules.compare(current, {}, limit=3).rows] == ["P3", "P2", "P1"]


def test_when_the_parts_cancel_out_there_is_no_overall_change_to_take_a_share_of():
    result = rules.compare(
        {"a": ("A", D(150)), "b": ("B", D(50))}, {"a": ("A", D(50)), "b": ("B", D(150))}
    )
    assert (
        result.total_change == 0 and result.total_change_pct is None or result.total_change_pct == 0
    )
    assert [r.share_of_change_pct for r in result.rows] == [None, None]


def test_the_overall_per_cent_change_needs_something_to_compare_with():
    assert rules.compare({"a": ("A", D(10))}, {}).total_change_pct is None
    result = rules.compare({"a": ("A", D(60))}, {"a": ("A", D(80))})
    assert result.total_change_pct == D("-25")
    assert rules.compare({"a": ("A", D(10))}, {"a": ("A", D(-20))}).total_change_pct == D("150")


def test_the_sentences_say_what_a_part_did_and_how_much_of_the_change_it_was():
    def row(current, previous, total_change):
        c, p = D(current), D(previous)
        return rules.SegmentChange(
            "k",
            "Sourdough",
            c,
            p,
            c - p,
            rules.share_of(c - p, D(total_change)),
            rules.state_of(c, p),
        )

    say = rules.describe_segment
    assert say("Sales", "gbp", row(130, 400, -190), "March 2026", "February 2026") == (
        "Sourdough fell from £400.00 in February 2026 to £130.00 in March 2026. "
        "That is 142% of the overall change."
    )
    assert say("Sales", "gbp", row(80, 0, -190), "March 2026", "February 2026") == (
        "Sourdough had no sales in February 2026 and £80.00 in March 2026. "
        "That went against the overall change."
    )
    assert say("Sales", "gbp", row(0, 50, -50), "March 2026", "February 2026") == (
        "Sourdough had £50.00 in February 2026 and none in March 2026. "
        "That is 100% of the overall change."
    )
    assert say("Items sold", "count", row(40, 10, 0), "March 2026", "February 2026") == (
        "Sourdough rose from 10 in February 2026 to 40 in March 2026."
    )


# --- a shop worked out by hand --------------------------------------------------------------


def split(api, business, metric, dimension, query="", who="owner", org=0):
    res = api.get(
        f"{ORGS}/{business[org]}/segments/{metric}/{dimension}?month=2026-03-01{query}",
        headers=business[2][who],
    )
    assert res.status_code == 200, res.text
    return res.json()


def moves(body):
    return [(r["label"], r["change"], r["share_of_change_pct"], r["state"]) for r in body["rows"]]


def test_sales_by_product_show_which_products_the_fall_came_from(api, bakery):
    body = split(api, bakery, "revenue", "product")
    assert (body["total_previous"], body["total_current"], body["total_change"]) == (
        "600.00", "410.00", "-190.00",
    )  # fmt: skip
    assert body["total_change_pct"] == "-31.7"
    assert body["headline"] == (
        "Sales fell by £190.00 (32%) in March 2026 compared with February 2026."
    )
    # Cake sold the same in both months, so it is left out. Sourdough: 400 -> 130; Brownie is new.
    assert moves(body) == [
        ("Sourdough", "-270.00", "142.1", "down"),
        ("Brownie", "80.00", "-42.1", "new"),
    ]
    sourdough = body["rows"][0]
    assert (sourdough["previous"], sourdough["current"]) == ("400.00", "130.00")
    assert sourdough["text"] == (
        "Sourdough fell from £400.00 in February 2026 to £130.00 in March 2026. "
        "That is 142% of the overall change."
    )


def test_sales_by_channel(api, bakery):
    body = split(api, bakery, "revenue", "channel")
    assert moves(body) == [
        ("Shop", "-220.00", "115.8", "down"),
        ("Website", "30.00", "-15.8", "up"),
    ]


def test_sales_by_customer_with_the_sales_that_name_nobody_kept_apart(api, bakery):
    body = split(api, bakery, "revenue", "customer")
    assert moves(body) == [
        ("Jo", "-270.00", "142.1", "down"),
        ("No customer recorded", "80.00", "-42.1", "new"),
    ]
    assert body["rows"][1]["key"] == "none"


def test_sales_by_day_of_the_week(api, bakery):
    body = split(api, bakery, "revenue", "weekday")
    assert moves(body) == [
        ("Tuesday", "-200.00", "105.3", "down"),
        ("Saturday", "80.00", "-42.1", "new"),
        ("Thursday", "-50.00", "26.3", "down"),
        ("Friday", "-20.00", "10.5", "new"),  # the refund: negative sales on a Friday
    ]


def test_every_way_of_splitting_sales_adds_up_to_the_same_total(api, bakery):
    for dimension in ("product", "channel", "customer", "weekday"):
        body = split(api, bakery, "revenue", dimension, "&limit=50")
        assert (body["total_previous"], body["total_current"]) == ("600.00", "410.00"), dimension
        assert sum(D(r["change"]) for r in body["rows"]) == D("-190.00"), dimension


def test_the_number_of_sales_leaves_refunds_out(api, bakery):
    body = split(api, bakery, "sales_count", "channel")
    assert (body["total_previous"], body["total_current"], body["total_change"]) == ("3", "4", "1")
    assert moves(body) == [("Website", "1", "100.0", "up")]  # the Shop had two both months
    assert (
        body["headline"]
        == "Number of sales rose by 1 (33%) in March 2026 compared with February 2026."
    )


def test_items_sold_by_product_count_refunded_items_back(api, bakery):
    body = split(api, bakery, "units_sold", "product")
    # Sourdough: 130 -> 30 + 15 - 5 = 40. Brownie: 10 new. Cake: 20 both months.
    assert moves(body) == [("Sourdough", "-90", "112.5", "down"), ("Brownie", "10", "-12.5", "new")]
    assert (body["total_previous"], body["total_current"]) == ("150", "70")


def test_gross_profit_by_product_gives_the_cost_of_refunded_goods_back(api, bakery):
    body = split(api, bakery, "gross_profit", "product")
    # Sourdough Feb (300-150) + (100-50) = 200. Mar (100-50) + (50-25) + (-20 + 10) = 65.
    # Brownie 80 - 30 = 50 new. Cake 100 both months.
    assert moves(body) == [
        ("Sourdough", "-135.00", "158.8", "down"),
        ("Brownie", "50.00", "-58.8", "new"),
    ]
    assert (body["total_previous"], body["total_current"]) == ("300.00", "215.00")


def test_a_month_can_be_compared_with_the_same_month_last_year(api, bakery):
    body = split(api, bakery, "revenue", "product", "&against=last_year")
    assert body["against"] == "last_year" and body["compared_with"] == "2025-03-01"
    assert (body["total_previous"], body["total_current"]) == ("500.00", "410.00")
    assert moves(body) == [
        ("Sourdough", "-370.00", "411.1", "down"),
        ("Cake", "200.00", "-222.2", "new"),
        ("Brownie", "80.00", "-88.9", "new"),
    ]
    assert body["headline"].endswith("compared with March 2025.")


def test_any_day_in_the_month_will_do(api, bakery):
    res = api.get(
        f"{ORGS}/{bakery[0]}/segments/revenue/channel?month=2026-03-19", headers=bakery[2]["owner"]
    )
    assert res.json()["month"] == "2026-03-01"


def test_the_list_is_limited_and_the_rest_rolled_up(api, bakery):
    body = split(api, bakery, "revenue", "weekday", "&limit=2")
    assert [r["label"] for r in body["rows"]] == ["Tuesday", "Saturday", "Everything else"]
    rest = body["rows"][-1]
    assert rest["key"] is None and rest["change"] == "-70.00"  # Thursday -50 and Friday -20
    assert rest["current"] == "30.00" and rest["previous"] == "100.00"


def test_sales_with_no_product_detail_are_a_row_of_their_own_so_the_parts_add_up(api, db, business):
    cake = _make(db, business, Product, name="Cake")
    _sale(db, business, date(2026, 2, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 2, 4), 100)  # no lines
    _sale(db, business, date(2026, 3, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 3, 4), 30)  # no lines
    body = split(api, business, "revenue", "product")
    assert (body["total_previous"], body["total_current"]) == ("200.00", "130.00")
    assert moves(body) == [("Sales with no product detail", "-70.00", "100.0", "down")]
    assert body["rows"][0]["key"] == "no_detail"
    # Items sold only ever count what the lines say
    assert split(api, business, "units_sold", "product")["rows"] == []


def test_an_unnamed_customer_is_not_mistaken_for_no_customer(api, db, business):
    nameless = _make(db, business, Customer)
    _sale(db, business, date(2026, 3, 3), 50, customer=nameless)
    _sale(db, business, date(2026, 3, 4), 20)
    body = split(api, business, "revenue", "customer")
    assert sorted(r["label"] for r in body["rows"]) == ["No customer recorded", "Unnamed"]


def test_running_costs_by_category_leave_out_stock_bought_for_resale(api, costs):
    body = split(api, costs, "operating_expenses", "category")
    assert (body["total_previous"], body["total_current"], body["total_change"]) == (
        "600.00", "730.00", "130.00",
    )  # fmt: skip
    assert moves(body) == [
        ("Utilities", "80.00", "61.5", "up"),
        ("No category", "50.00", "38.5", "new"),
    ]
    assert (
        body["headline"]
        == "Running costs rose by £130.00 (22%) in March 2026 compared with February 2026."
    )


def test_running_costs_by_supplier(api, costs):
    body = split(api, costs, "operating_expenses", "supplier")
    assert (body["total_previous"], body["total_current"]) == ("600.00", "730.00")
    # The Landlord was the same; the stock (Miller) is not a running cost; the rest has no supplier
    assert moves(body) == [("No supplier", "130.00", "100.0", "up")]


def test_asking_for_something_that_cannot_be_split_is_refused_with_the_choices(api, bakery):
    base = f"{ORGS}/{bakery[0]}/segments"
    headers = bakery[2]["owner"]
    nope = api.get(f"{base}/gross_profit/channel?month=2026-03-01", headers=headers)
    assert nope.status_code == 422 and "product" in nope.json()["error"]["message"]
    unknown = api.get(f"{base}/happiness/product?month=2026-03-01", headers=headers)
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "bad_metric"
    assert api.get(f"{base}/revenue/product", headers=headers).status_code == 422  # no month
    assert (
        api.get(f"{base}/revenue/product?month=2026-03-01&limit=0", headers=headers).status_code
        == 422
    )
    assert (
        api.get(
            f"{base}/revenue/product?month=2026-03-01&against=never", headers=headers
        ).status_code
        == 422
    )


def test_the_figures_that_can_be_split_are_listed(api, bakery):
    res = api.get(f"{ORGS}/{bakery[0]}/segments", headers=bakery[2]["owner"])
    assert res.json()["metrics"] == {
        "revenue": ["product", "channel", "customer", "weekday"],
        "sales_count": ["channel", "customer", "weekday"],
        "units_sold": ["product"],
        "gross_profit": ["product"],
        "operating_expenses": ["category", "supplier"],
    }


def test_a_month_with_no_change_says_so(api, db, business):
    _sale(db, business, date(2026, 2, 3), 100)
    _sale(db, business, date(2026, 3, 3), 100)
    body = split(api, business, "revenue", "weekday")
    assert body["rows"] == [] and body["total_change_pct"] == "0.0"
    assert body["headline"] == "Sales in March 2026 was the same as in February 2026."


def test_a_business_with_no_records_gets_an_empty_answer(api, business):
    body = split(api, business, "revenue", "product")
    assert (
        body["rows"] == [] and body["total_current"] == "0.00" and body["total_change_pct"] is None
    )


def test_each_business_sees_only_its_own_records(api, bakery):
    rival = split(api, bakery, "revenue", "channel", who="other", org=1)
    assert rival["rows"] == [] and rival["total_current"] == "0.00"
    assert (
        api.get(
            f"{ORGS}/{bakery[1]}/segments/revenue/channel?month=2026-03-01",
            headers=bakery[2]["owner"],
        ).status_code
        == 404
    )


def test_viewers_can_look_and_strangers_cannot(api, bakery):
    assert split(api, bakery, "revenue", "channel", who="viewer")["rows"]
    url = f"{ORGS}/{bakery[0]}/segments/revenue/channel?month=2026-03-01"
    assert api.get(url).status_code == 401


def test_gross_profit_counts_a_sale_with_no_product_detail_as_all_profit_like_the_key_figure(
    api, db, business
):
    cake = _make(db, business, Product, name="Cake")
    _sale(db, business, date(2026, 2, 3), 100, line=(cake, 10, 40))  # profit 60
    _sale(db, business, date(2026, 2, 4), 100)  # no lines, so no cost is known: profit 100
    _sale(db, business, date(2026, 3, 3), 100, line=(cake, 10, 40))
    body = split(api, business, "gross_profit", "product")
    assert (body["total_previous"], body["total_current"]) == ("160.00", "60.00")
    assert moves(body) == [("Sales with no product detail", "-100.00", "100.0", "gone")]


def test_the_parts_add_up_to_the_figures_on_the_key_figures_page(api, db, bakery, costs):
    from sqlalchemy import select

    from app.models.kpi import KpiDefinition, KpiValue
    from app.services import kpi
    from tests.test_health import owner_tenant

    with scoped(db, bakery):
        kpi.calculate(db, owner_tenant(db, bakery), granularity="month", first=date(2026, 2, 1),
                      last=date(2026, 3, 1))  # fmt: skip
        rows = db.execute(
            select(KpiDefinition.code, KpiValue.period_start, KpiValue.value)
            .join(KpiValue, KpiValue.kpi_id == KpiDefinition.id)
            .where(KpiValue.granularity == "month")
        ).all()
    figure = {(code, month): value for code, month, value in rows}
    for metric, dimensions in {
        "revenue": ("product", "channel", "customer", "weekday"),
        "sales_count": ("channel", "customer", "weekday"),
        "units_sold": ("product",),
        "gross_profit": ("product",),
        "operating_expenses": ("category", "supplier"),
    }.items():
        for dimension in dimensions:
            body = split(api, bakery, metric, dimension, "&limit=50")
            assert D(body["total_current"]) == figure[(metric, date(2026, 3, 1))], (
                metric,
                dimension,
            )
            assert D(body["total_previous"]) == figure[(metric, date(2026, 2, 1))], (
                metric,
                dimension,
            )


def test_many_parts_that_moved_equally_always_come_out_in_name_order():
    current = {f"k{i}": (f"Part {chr(ord('a') + (i * 7) % 12)}", D(10)) for i in range(12)}
    labels = [r.label for r in rules.compare(current, {}, limit=50).rows]
    assert labels == sorted(labels) and len(labels) == 12


def test_parts_rolled_up_that_cancel_out_are_neither_for_nor_against_the_change():
    result = rules.compare(
        {"a": ("A", D(100)), "b": ("B", D(10)), "c": ("C", D(0))},
        {"a": ("A", D(0)), "b": ("B", D(0)), "c": ("C", D(10))},
        limit=1,
    )
    rest = result.rows[-1]
    assert (rest.label, rest.change, rest.share_of_change_pct) == ("Everything else", 0, 0)
    assert rules.describe_segment("Sales", "gbp", rest, "March 2026", "February 2026").endswith(
        "That is 0% of the overall change."
    )


def test_sunday_is_the_seventh_day_not_the_zeroth(api, db, business):
    _sale(db, business, date(2026, 2, 8), 100)  # a Sunday
    _sale(db, business, date(2026, 3, 8), 160)  # a Sunday
    [row] = split(api, business, "revenue", "weekday")["rows"]
    assert (row["label"], row["key"], row["change"]) == ("Sunday", "7", "60.00")
