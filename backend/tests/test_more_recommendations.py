# ruff: noqa: E501
"""Slow stock, protecting the best seller and a valuable customer at risk (client feedback
15/10/2026, section 4, examples B, C and H)."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models.data import Customer, Product, StockMovement
from app.models.recommendations import Intervention
from app.recommend import rules
from app.services import finding_tags
from tests.shops import _make, _sale
from tests.test_health import scoped
from tests.test_recommendations import action, event_for, finding, recommend

TODAY = date(2026, 10, 17)


def part(label, **kw):
    return finding("contributor", "parts", label, amount="-300", share="60", **kw)


def tagged(f, *tags):
    f.tags = frozenset(tags)
    return f


# --- matching: a tag, and good news ---------------------------------------------------------------


def spec_action(**spec):
    a = action(kinds=(), kpis=("revenue",))
    a.addresses = [{"kind": "contributor", "dimension": "Product", **spec}]
    return a


def test_an_action_that_needs_a_tag_is_only_called_up_by_a_finding_with_it():
    slow = spec_action(tag="much_stock")
    plain = part("Product: Sourdough")
    assert not rules.matches(slow, plain, "revenue")
    assert rules.matches(slow, tagged(plain, "much_stock"), "revenue")
    assert not rules.matches(slow, tagged(plain, "low_stock"), "revenue")


def test_an_action_with_no_tag_still_matches_whatever_the_finding_carries():
    assert rules.matches(spec_action(), tagged(part("Product: Sourdough"), "much_stock"), "revenue")


def test_an_action_for_good_news_is_never_suggested_for_a_fall_and_the_reverse():
    keep = spec_action(effect="good")
    fix = spec_action()
    f = part("Product: Sourdough")
    assert not rules.matches(keep, f, "revenue", "bad") and rules.matches(
        keep, f, "revenue", "good"
    )
    assert rules.matches(fix, f, "revenue", "bad") and not rules.matches(fix, f, "revenue", "good")


def test_generate_takes_the_effect_into_account():
    keep, fix = spec_action(effect="good"), spec_action()
    keep.code, fix.code = "keep", "fix"
    found = [part("Product: Sourdough")]
    assert [c.action.code for c in rules.generate([keep, fix], found, "revenue")] == ["fix"]
    assert [c.action.code for c in rules.generate([keep, fix], found, "revenue", "good")] == [
        "keep"
    ]


# --- the three new entries in the library ----------------------------------------------------------


@pytest.fixture
def library(db):
    return {i.code: i for i in db.scalars(select(Intervention))}


def test_the_three_are_in_the_library(library):
    assert {
        "slow_stock_review",
        "protect_best_seller",
        "valuable_customer_at_risk",
    } <= library.keys()


def test_slow_stock_offers_the_three_choices_and_warns_against_repeating_a_failed_promotion(
    library,
):
    item = library["slow_stock_review"]
    text = " ".join([item.summary, *item.steps])
    assert (
        "order less or pause" in text and "review the price" in text and "limited promotion" in text
    )
    assert "do not repeat it automatically" in text
    assert item.addresses == [{"kind": "contributor", "dimension": "Product", "tag": "much_stock"}]


def test_protecting_the_best_seller_says_no_needless_discount_and_promises_no_money(library):
    item = library["protect_best_seller"]
    assert "Do not discount it while demand is strong" in item.steps
    assert D(item.impact_share) == 0 and item.addresses[0]["effect"] == "good"
    assert any("profit, not sales alone" in s for s in item.steps)


def test_the_valuable_customer_is_contacted_within_three_days_and_not_called_saved(library):
    item = library["valuable_customer_at_risk"]
    assert item.typical_days_to_effect == 3
    assert any("within three days" in s for s in item.steps)
    assert any("Do not count them as saved" in s for s in item.steps)


# --- the facts behind the tags -----------------------------------------------------------------------


def stock(db, business, product, quantity):
    _make(
        db,
        business,
        StockMovement,
        product_id=product,
        kind="opening",
        quantity=D(quantity),
        moved_on=date(2026, 1, 1),
    )


def product_named(db, business, name):
    with scoped(db, business):
        return db.scalars(select(Product).where(Product.name == name)).one().id


def tags_of(db, business, label):
    with scoped(db, business):
        [f] = finding_tags.tag(db, [part(label)], TODAY)
        return set(f.tags)


def test_a_product_with_a_full_shelf_and_slow_sales_has_much_stock(db, march):
    stock(db, march, product_named(db, march, "Sourdough"), 500)
    assert tags_of(db, march, "Product: Sourdough") == {"much_stock"}


def test_a_product_that_is_nearly_gone_has_low_stock(db, march):
    cake = product_named(db, march, "Cake")
    stock(db, march, cake, 2)
    # sold lately at a pace that would use it up in days
    _sale(db, march, TODAY - timedelta(days=5), 200, line=(cake, 60, 100))
    assert tags_of(db, march, "Product: Cake") == {"low_stock"}


def test_a_product_with_about_the_right_stock_has_neither(db, march):
    cake = product_named(db, march, "Cake")
    stock(db, march, cake, 60)
    _sale(
        db, march, TODAY - timedelta(days=5), 200, line=(cake, 90, 100)
    )  # 1 a day: 60 days of cover
    assert tags_of(db, march, "Product: Cake") == set()


def test_when_no_stock_is_recorded_nothing_is_said_about_it(db, march):
    assert tags_of(db, march, "Product: Sourdough") == set()


def test_a_customer_who_bought_lately_is_still_buying_and_one_who_did_not_is_not(db, march):
    jo = _make(db, march, Customer, name="Ola")
    _sale(db, march, date(2026, 3, 14), 50, customer=jo)  # the latest sale in the books is 20/03
    assert "still_buying" in tags_of(db, march, "Customer: Ola")
    old = _make(db, march, Customer, name="Ife")
    _sale(db, march, date(2025, 12, 1), 50, customer=old)
    assert tags_of(db, march, "Customer: Ife") == set()


def test_other_kinds_of_finding_are_left_alone(db, march):
    with scoped(db, march):
        [f] = finding_tags.tag(db, [finding("sale_value")], TODAY)
    assert f.tags == frozenset()


# --- end to end ---------------------------------------------------------------------------------------


def test_a_falling_product_with_a_full_shelf_is_offered_the_slow_stock_review(
    api, db, march, monkeypatch
):
    stock(db, march, product_named(db, march, "Sourdough"), 5000)
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    assert body["status"] == "open"
    titles = [o["title"] for o in body["options"]]
    assert "Review the slow-moving stock before you reorder: Sourdough" in titles, titles


def test_without_stock_records_the_slow_stock_review_is_not_offered(api, march):
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    assert not any("slow-moving stock" in o["title"] for o in body["options"])


@pytest.fixture
def boom(db, bakery):
    """The bakery's March, with a big run of brownies added: sales rise and brownies drive it."""
    from app.services import detection, kpi
    from tests.test_health import owner_tenant

    brownie = product_named(db, bakery, "Brownie")
    _sale(db, bakery, date(2026, 3, 16), 700, line=(brownie, 100, 200))
    with scoped(db, bakery):
        tenant = owner_tenant(db, bakery)
        kpi.calculate(
            db, tenant, granularity="month", first=date(2026, 2, 1), last=date(2026, 3, 1)
        )
        detection.detect(db, tenant)
    return bakery


def test_a_rise_driven_by_one_product_suggests_protecting_it(api, boom):
    event = event_for(api, boom, "revenue")
    assert event["effect"] == "good"
    body = recommend(api, boom, event).json()
    assert body["status"] == "open"
    assert (
        body["options"][0]["title"]
        == "Protect your best seller: check stock and avoid needless discounts: Brownie"
    )
    assert "win back" not in body["options"][0]["description"]  # nothing is being won back
