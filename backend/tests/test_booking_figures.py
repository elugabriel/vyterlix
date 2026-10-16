# ruff: noqa: E501
"""Appointment figures (client feedback 15/10/2026, §6), checked against numbers worked out by hand."""

from datetime import date

import pytest

from app.models.data import Booking
from app.plain.figures import WORDS
from tests.test_kpi_customers_stock import customer, value
from tests.test_kpi_engine import run, scoped

CODES = {
    "appointments_kept",
    "cancellation_rate_pct",
    "no_show_rate_pct",
    "late_cancellations",
    "rebooking_rate_pct",
}


def book(db, business, day, who=None, status="completed", cancelled_on=None, service="Cut"):
    with scoped(db, business):
        db.add(
            Booking(
                booked_for=day, service=service, status=status, customer_id=who,
                cancelled_on=cancelled_on,
            )
        )  # fmt: skip
        db.flush()


@pytest.fixture
def salon(db, business):
    """September 2026, by hand.

    Jo: kept 1st and 15th; has another booked 2 Oct.   Sam: kept 2nd; missed the 20th; nothing after.
    Ana: cancelled the 10th (on the 1st) and the 3rd (on the 3rd, so late).
    Ola: kept the 25th; has another booked 5 Oct.   A booking with no customer was kept on the 8th.

    Kept 5 (Jo 2, Sam, Ola, anonymous), cancelled 2, missed 1: 8 finished. Late cancellations 1.
    Customers who kept one: Jo, Sam, Ola = 3; with another booked after their last: Jo, Ola = 2.
    """
    jo, sam, ana, ola = (customer(db, business, n) for n in ("Jo", "Sam", "Ana", "Ola"))
    s = lambda *a, **k: book(db, business, *a, **k)  # noqa: E731
    s(date(2026, 9, 1), jo)
    s(date(2026, 9, 15), jo)
    s(date(2026, 9, 2), sam)
    s(date(2026, 9, 20), sam, status="no_show")
    s(date(2026, 9, 10), ana, status="cancelled", cancelled_on=date(2026, 9, 1))
    s(date(2026, 9, 3), ana, status="cancelled", cancelled_on=date(2026, 9, 3))
    s(date(2026, 9, 25), ola)
    s(date(2026, 9, 8))
    s(date(2026, 10, 2), jo, status="booked")
    s(date(2026, 10, 5), ola, status="booked")
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, salon, code, key="value"):
    return value(api, salon, code, "2026-09-01", key)


def test_the_counts_and_rates_match_the_hand_worked_numbers(api, salon):
    assert sept(api, salon, "appointments_kept") == "5"
    assert sept(api, salon, "late_cancellations") == "1"
    assert sept(api, salon, "cancellation_rate_pct") == "25.00"  # 2 of 8
    assert sept(api, salon, "no_show_rate_pct") == "12.50"  # 1 of 8


def test_rebooking_counts_customers_with_a_later_appointment(api, salon):
    assert sept(api, salon, "rebooking_rate_pct") == "66.67"  # Jo and Ola of Jo, Sam, Ola


def test_an_appointment_still_to_come_is_in_no_rate(api, salon):
    assert value(api, salon, "appointments_kept", "2026-10-01") == "0"
    assert value(api, salon, "cancellation_rate_pct", "2026-10-01", "status") == "undefined"


def test_without_any_bookings_the_figures_say_what_they_need(api, db, business):
    from tests.test_kpi_customers_stock import sell
    from tests.test_kpi_engine import history

    sell(db, business, date(2026, 9, 3), "50")  # sales, but no appointments
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    body = history(api, business, "no_show_rate_pct").json()
    assert body["values"][0]["status"] == "no_data" and body["requires"] == ["bookings"]


def kpi_codes(api, business, who="owner"):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2][who])
    return {k["code"] for k in res.json()["kpis"]}


def say(api, business, sells):
    from tests.test_business_profile_api import MINIMAL, url

    res = api.put(url(business[0]), json={**MINIMAL, "sells": sells}, headers=business[2]["owner"])
    assert res.status_code == 200, res.text


def test_a_business_that_sells_products_is_not_shown_appointment_figures(api, db, business):
    say(api, business, "products")
    assert not CODES & kpi_codes(api, business)


@pytest.mark.parametrize("sells", ["services", "both"])
def test_a_business_that_sells_a_service_is_shown_them_even_before_any_booking(
    api, db, business, sells
):
    say(api, business, sells)
    assert CODES <= kpi_codes(api, business)


def test_a_business_that_has_not_said_sees_them_once_it_has_bookings(api, db, business):
    assert not CODES & kpi_codes(api, business)
    book(db, business, date(2026, 9, 1))
    assert CODES <= kpi_codes(api, business)


def test_every_new_figure_has_plain_words_and_no_cause_is_claimed(api):
    for code in CODES:
        words = WORDS[code]
        for say_ in (words.up, words.down):
            assert say_.says.strip() and say_.why.strip()
            assert "because" not in (say_.says + say_.why).lower()
