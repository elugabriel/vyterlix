# ruff: noqa: E501, F811
"""Health rules for message replies, leaning on one supplier, suppliers' credits, sales and
appointments against last year, and clients who have gone quiet."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.models.data import CustomerMessage
from app.models.health import HealthRule
from app.modules.rules import health_area_counts
from tests.test_booking_figures import say
from tests.test_health import Kpis, calculate, component, get, scoped

NEW = {
    "replied_within_hour_pct": ("operational", "value", "up_good"),
    "average_reply_minutes": ("operational", "value", "down_good"),
    "unanswered_conversations": ("operational", "vs_baseline", "down_good"),
    "top_supplier_share_pct": ("financial", "value", "down_good"),
    "supplier_credit_rate_pct": ("financial", "value", "down_good"),
    "sales_count_vs_last_year_pct": ("sales", "value", "up_good"),
    "bookings_vs_last_year_pct": ("sales", "value", "up_good"),
    "lapsed_clients": ("customer", "vs_baseline", "down_good"),
}
# Counts that are neither good nor bad alone, and month-on-month growth that swings with the seasons.
UNJUDGED = {
    "messages_received", "conversations_started", "supplier_bought", "suppliers_used",
    "sales_count_growth_pct", "units_growth_pct", "bookings_growth_pct", "clients_seen",
}  # fmt: skip


def a_message(db, business):
    """One message on record, so the business counts as one that takes messages."""
    with scoped(db, business):
        db.add(
            CustomerMessage(
                sent_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC), direction="in", contact_ref="A",
                source="whatsapp", source_ref="m1",
            )
        )  # fmt: skip
        db.flush()


def rules(db):
    return {
        r.kpi_code: r
        for r in db.scalars(select(HealthRule).where(HealthRule.industry_code.is_(None)))
    }


def metric(body, category, code):
    return next(m for m in component(body, category)["metrics"] if m["kpi_code"] == code)


def test_the_new_rules_exist_judge_the_right_way_round_and_are_all_switched_on(db):
    found = rules(db)
    for code, (category, basis, direction) in NEW.items():
        rule = found[code]
        assert (rule.category, rule.basis, rule.direction, rule.is_active) == (
            category,
            basis,
            direction,
            True,
        ), code
        assert rule.weight > 0 and rule.note


def test_plain_counts_and_seasonal_growth_are_deliberately_not_judged(db):
    assert not UNJUDGED & set(rules(db))


def test_fast_replies_score_a_hundred_middling_ones_sixty_and_slow_ones_nothing(db, api, business):
    a_message(db, business)
    scores = {}
    for label, quick, wait in (("fast", 85, 15), ("middling", 60, 60), ("slow", 30, 240)):
        with scoped(db, business):
            db.execute(text("DELETE FROM kpi_values"))
        kpis = Kpis(db, business)
        kpis.months("replied_within_hour_pct", [quick] * 4)
        kpis.months("average_reply_minutes", [wait] * 4)
        calculate(db, business)
        scores[label] = component(get(api, business).json(), "operational")["score"]
    assert scores == {"fast": 100, "middling": 60, "slow": 0}


def test_conversations_going_unanswered_more_than_usual_lowers_the_score(db, api, business):
    a_message(db, business)
    kpis = Kpis(db, business)
    kpis.months(
        "unanswered_conversations", [4, 4, 4, 6]
    )  # half as many again as usual: the "bad" anchor is +50%
    calculate(db, business)
    assert (
        metric(get(api, business).json(), "operational", "unanswered_conversations")["score"] == 0.0
    )


def test_leaning_on_one_supplier_and_suppliers_giving_money_back_are_judged(db, api, business):
    kpis = Kpis(db, business)
    kpis.months("top_supplier_share_pct", [60] * 3)
    kpis.months("supplier_credit_rate_pct", [1] * 3)
    kpis.months("revenue", [1000] * 3)
    calculate(db, business)
    body = get(api, business).json()
    assert metric(body, "financial", "top_supplier_share_pct")["score"] == 60.0
    assert metric(body, "financial", "supplier_credit_rate_pct")["score"] == 100.0


def test_growth_on_last_year_and_clients_gone_quiet_are_judged(db, api, business):
    kpis = Kpis(db, business)
    kpis.months("sales_count_vs_last_year_pct", [0] * 4)
    kpis.months("bookings_vs_last_year_pct", [10] * 4)
    kpis.months("lapsed_clients", [5] * 4)
    kpis.months("revenue", [1000] * 4)
    kpis.months("active_customers", [10] * 4)
    calculate(db, business)
    body = get(api, business).json()
    assert metric(body, "sales", "sales_count_vs_last_year_pct")["score"] == 60.0
    assert metric(body, "sales", "bookings_vs_last_year_pct")["score"] == 100.0
    assert metric(body, "customer", "lapsed_clients")["score"] == 60.0  # exactly as usual


def test_a_business_with_no_messages_is_not_judged_on_replies_even_if_figures_exist(
    db, api, business
):
    kpis = Kpis(db, business)
    kpis.months("replied_within_hour_pct", [30] * 4)
    kpis.months("revenue", [1000] * 4)
    calculate(db, business)
    body = get(api, business).json()
    assert "operational" not in {c["category"] for c in body["components"]}


def test_a_shop_that_only_sells_products_is_judged_on_how_fast_it_answers_messages(
    db, api, business
):
    say(api, business, "products")
    a_message(db, business)
    kpis = Kpis(db, business)
    kpis.months("replied_within_hour_pct", [85] * 4)
    kpis.months("revenue", [1000] * 4)
    calculate(db, business)
    assert component(get(api, business).json(), "operational")["score"] == 100


@pytest.mark.parametrize(
    ("sells", "has_bookings", "has_messages", "counts"),
    [
        ("products", False, False, False),  # a shop with no messages: no appointment area
        ("products", False, True, True),  # a shop that answers messages: that area counts
        ("products", True, False, False),
        ("services", False, False, True),
        (None, False, False, False),
        (None, False, True, True),
    ],
)
def test_the_operations_area_counts_for_any_business_with_messages(
    sells, has_bookings, has_messages, counts
):
    assert health_area_counts("operational", sells, has_bookings, has_messages) is counts


def test_other_areas_are_unaffected_by_messages():
    assert health_area_counts("financial", "products", False, True) is True
    assert health_area_counts("inventory", "services", False, True) is False
