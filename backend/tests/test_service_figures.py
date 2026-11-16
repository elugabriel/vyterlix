# ruff: noqa: E501, F811
"""Figures for service businesses (client feedback 15/11/2026): bookings, retention, operational
performance and the team's time, checked against numbers worked out by hand."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.data import Booking, StaffHours
from app.plain.figures import WORDS
from tests.shops import _sale
from tests.test_booking_figures import kpi_codes, say
from tests.test_kpi_customers_stock import customer, value
from tests.test_kpi_engine import run, scoped

D = Decimal
ORGS = "/api/v1/organizations"

NEW = {
    "appointments_booked", "completion_rate_pct", "visits_per_customer", "new_clients",
    "average_appointment_minutes", "overrun_pct", "hours_worked", "revenue_per_hour",
    "utilisation_pct", "appointments_per_hour",
}  # fmt: skip


def book(
    db, business, day, who=None, status="completed", planned=None, taken=None, cancelled_on=None
):
    with scoped(db, business):
        db.add(
            Booking(
                booked_for=day, service="Cut", status=status, customer_id=who,
                minutes_planned=planned, minutes_taken=taken, cancelled_on=cancelled_on,
            )
        )  # fmt: skip
        db.flush()


def work(db, business, day, staff, hours):
    with scoped(db, business):
        db.add(StaffHours(worked_on=day, staff=staff, hours=D(hours)))
        db.flush()


@pytest.fixture
def salon(db, business):
    """September 2026, by hand.

    Jo kept 1st (planned 60, took 50) and 15th (planned 60, took 75). Sam kept the 2nd (30, took 30)
    and missed the 20th. Ana cancelled the 10th. Ola kept the 25th (no plan, took 45). Someone with
    no name kept the 8th (planned 30, took 40).

    Kept 5, cancelled 1, missed 1: 7 appointments, 7 finished, 5 of 7 went ahead = 71.43%.
    Jo, Sam and Ola kept 4 of those between them: 4 / 3 = 1.33 visits each. All three were new.
    Time taken 50 + 75 + 30 + 45 + 40 = 240 minutes over 5 appointments = 48 on average.
    Planned and taken both known: Jo twice, Sam, the anonymous one = 4; Jo's 15th (75 > 60) and the
    anonymous one (40 > 30) ran over = 50%.
    Staff: Mia 8 hours on the 1st and 8 on the 2nd, Ben 4 on the 15th = 20 hours.
    Sales 400 and 200 before VAT = 600, so 30 pounds an hour. 240 minutes = 4 of 20 hours = 20%
    of the time. 5 appointments over 20 hours = 0.25 an hour.
    """
    jo, sam, ana, ola = (customer(db, business, n) for n in ("Jo", "Sam", "Ana", "Ola"))
    book(db, business, date(2026, 9, 1), jo, planned=60, taken=50)
    book(db, business, date(2026, 9, 15), jo, planned=60, taken=75)
    book(db, business, date(2026, 9, 2), sam, planned=30, taken=30)
    book(db, business, date(2026, 9, 20), sam, status="no_show")
    book(db, business, date(2026, 9, 10), ana, status="cancelled", cancelled_on=date(2026, 9, 1))
    book(db, business, date(2026, 9, 25), ola, taken=45)
    book(db, business, date(2026, 9, 8), None, planned=30, taken=40)
    work(db, business, date(2026, 9, 1), "Mia", "8")
    work(db, business, date(2026, 9, 2), "Mia", "8")
    work(db, business, date(2026, 9, 15), "Ben", "4")
    _sale(db, business, date(2026, 9, 3), 400)
    _sale(db, business, date(2026, 9, 20), 200)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, salon, code, key="value"):
    return value(api, salon, code, "2026-09-01", key)


def test_the_booking_and_retention_figures_match_the_hand_worked_numbers(api, salon):
    assert sept(api, salon, "appointments_booked") == "7"
    assert sept(api, salon, "completion_rate_pct") == "71.43"
    assert sept(api, salon, "visits_per_customer") == "1.33"
    assert sept(api, salon, "new_clients") == "3"


def test_the_operational_figures_match_the_hand_worked_numbers(api, salon):
    assert sept(api, salon, "average_appointment_minutes") == "48"
    assert sept(api, salon, "overrun_pct") == "50.00"


def test_the_workforce_figures_match_the_hand_worked_numbers(api, salon):
    assert sept(api, salon, "hours_worked") == "20"
    assert sept(api, salon, "revenue_per_hour") == "30.00"
    assert sept(api, salon, "utilisation_pct") == "20.00"
    assert sept(api, salon, "appointments_per_hour") == "0.25"


def test_a_client_is_new_only_in_the_month_of_their_first_kept_appointment(api, db, business):
    jo = customer(db, business, "Jo")
    book(db, business, date(2026, 8, 5), jo)
    book(db, business, date(2026, 9, 5), jo)
    run(db, business, first=date(2026, 8, 1), today=date(2026, 11, 15))
    assert value(api, business, "new_clients", "2026-08-01") == "1"
    assert value(api, business, "new_clients", "2026-09-01") == "0"


def test_without_hours_typed_in_the_workforce_figures_are_not_shown_at_all(api, db, business):
    say(api, business, "services")
    book(db, business, date(2026, 9, 1), None, planned=60, taken=50)
    codes = kpi_codes(api, business)
    assert "completion_rate_pct" in codes and "average_appointment_minutes" in codes
    assert (
        not {"hours_worked", "revenue_per_hour", "utilisation_pct", "appointments_per_hour"} & codes
    )


def test_without_any_times_typed_in_the_time_figures_are_not_shown(api, db, business):
    say(api, business, "services")
    book(db, business, date(2026, 9, 1), None)
    codes = kpi_codes(api, business)
    assert not {"average_appointment_minutes", "overrun_pct", "utilisation_pct"} & codes


def test_a_new_module_appears_once_its_figures_do(api, db, salon):
    body = api.get(f"{ORGS}/{salon[0]}/kpis", headers=salon[2]["owner"]).json()
    modules = {m["code"] for m in body["modules"]}
    assert {"bookings", "retention", "operations", "workforce"} <= modules


def test_every_new_figure_has_plain_words_and_no_cause_is_claimed():
    for code in NEW:
        for say_ in (WORDS[code].up, WORDS[code].down):
            assert say_.says.strip() and say_.why.strip()
            assert "because" not in (say_.says + say_.why).lower()


# --- typing it in --------------------------------------------------------------------------------


def post(api, business, path, body, who="owner"):
    return api.post(f"{ORGS}/{business[0]}/{path}", json=body, headers=business[2][who])


BOOKING = {"booked_for": "2026-09-01", "service": "Cut", "status": "completed"}


def test_an_appointment_can_say_how_long_it_was_planned_for_and_took(api, business):
    res = post(api, business, "bookings", {**BOOKING, "minutes_planned": 60, "minutes_taken": 75})
    assert res.status_code == 201, res.text
    assert (res.json()["minutes_planned"], res.json()["minutes_taken"]) == (60, 75)


def test_both_times_are_optional(api, business):
    res = post(api, business, "bookings", BOOKING)
    assert res.json()["minutes_planned"] is None and res.json()["minutes_taken"] is None


@pytest.mark.parametrize("minutes", [0, -5, 1441])
def test_a_time_must_be_between_a_minute_and_a_day(api, business, minutes):
    assert post(api, business, "bookings", {**BOOKING, "minutes_taken": minutes}).status_code == 422
    assert (
        post(api, business, "bookings", {**BOOKING, "minutes_planned": minutes}).status_code == 422
    )


def test_only_an_appointment_that_went_ahead_has_a_time_taken(api, business):
    res = post(api, business, "bookings", {**BOOKING, "status": "booked", "minutes_taken": 30})
    assert res.status_code == 422
    planned = post(
        api, business, "bookings", {**BOOKING, "status": "booked", "minutes_planned": 30}
    )
    assert planned.status_code == 201  # how long it is meant to take is known before it happens


def test_changing_an_appointment_to_missed_clears_the_time_it_took(api, business):
    made = post(api, business, "bookings", {**BOOKING, "minutes_taken": 30}).json()
    res = api.patch(
        f"{ORGS}/{business[0]}/bookings/{made['id']}",
        json={"status": "no_show"},
        headers=business[2]["owner"],
    )
    assert res.status_code == 200 and res.json()["minutes_taken"] is None


HOURS = {"worked_on": "2026-09-01", "staff": "Mia", "hours": "7.5"}


def test_hours_can_be_typed_in_listed_changed_and_removed(api, business):
    made = post(api, business, "staff-hours", HOURS)
    assert made.status_code == 201, made.text
    row = made.json()
    assert (row["staff"], D(row["hours"])) == ("Mia", D("7.5"))
    listed = api.get(f"{ORGS}/{business[0]}/staff-hours", headers=business[2]["owner"]).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == row["id"]
    patched = api.patch(
        f"{ORGS}/{business[0]}/staff-hours/{row['id']}",
        json={"hours": "8"},
        headers=business[2]["owner"],
    )
    assert D(patched.json()["hours"]) == D("8")
    gone = api.delete(f"{ORGS}/{business[0]}/staff-hours/{row['id']}", headers=business[2]["owner"])
    assert gone.status_code == 204
    assert (
        api.get(f"{ORGS}/{business[0]}/staff-hours", headers=business[2]["owner"]).json()["total"]
        == 0
    )


@pytest.mark.parametrize("hours", ["0", "-1", "24.5", "abc"])
def test_hours_must_be_more_than_nothing_and_no_more_than_a_day(api, business, hours):
    assert post(api, business, "staff-hours", {**HOURS, "hours": hours}).status_code == 422


def test_a_name_is_needed(api, business):
    assert post(api, business, "staff-hours", {**HOURS, "staff": "  "}).status_code == 422


def test_a_viewer_cannot_type_hours_in_but_can_be_refused_politely(api, business):
    assert post(api, business, "staff-hours", HOURS, who="viewer").status_code == 403


def test_the_list_can_be_narrowed_by_day_and_person(api, business):
    post(api, business, "staff-hours", HOURS)
    post(api, business, "staff-hours", {**HOURS, "staff": "Ben", "worked_on": "2026-09-10"})
    url = f"{ORGS}/{business[0]}/staff-hours"
    auth = business[2]["owner"]
    assert api.get(url, params={"staff": "ben"}, headers=auth).json()["total"] == 1
    assert api.get(url, params={"date_from": "2026-09-05"}, headers=auth).json()["total"] == 1
    assert api.get(url, headers=auth).json()["items"][0]["staff"] == "Ben"  # newest day first


def test_one_business_cannot_see_anothers_hours(api, db, signup):
    owner = signup("owner@acme.co.uk")
    a = api.post(ORGS, json={"name": "Acme Salon"}, headers=owner).json()["id"]
    b = api.post(ORGS, json={"name": "Acme Shop"}, headers=owner).json()["id"]
    api.post(f"{ORGS}/{a}/staff-hours", json=HOURS, headers=owner)
    assert api.get(f"{ORGS}/{b}/staff-hours", headers=owner).json()["total"] == 0


# --- clients, and what each appointment brings in and costs -------------------------------------


def test_the_clients_who_have_gone_quiet_are_counted_by_hand(api, db, business):
    """Judged at the end of September 2026: Jo was last seen in March and Ana in October 2025, both
    between a year and three months ago, so both have gone quiet. Sam was seen in August, so has not.
    Ola was last seen in August 2024, over a year ago, so is long gone and not counted."""
    jo, sam, ana, ola = (customer(db, business, n) for n in ("Jo", "Sam", "Ana", "Ola"))
    book(db, business, date(2026, 3, 10), jo)
    book(db, business, date(2026, 8, 15), sam)
    book(db, business, date(2025, 10, 5), ana)
    book(db, business, date(2024, 8, 1), ola)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    assert value(api, business, "lapsed_clients", "2026-09-01") == "2"
    assert value(api, business, "clients_seen", "2026-09-01") == "0"


def test_what_each_appointment_brings_in_costs_and_leaves_matches_the_hand_worked_numbers(
    api, db, business
):
    """Five appointments kept in September, sales of 600 and running costs of 100: 120 brought in,
    20 spent and 100 left for each appointment."""
    from tests.shops import _expense

    jo, sam, ola = (customer(db, business, n) for n in ("Jo", "Sam", "Ola"))
    for who, day in ((jo, 1), (jo, 8), (sam, 9), (ola, 15), (sam, 22)):
        book(db, business, date(2026, 9, day), who)
    _sale(db, business, date(2026, 9, 3), 400)
    _sale(db, business, date(2026, 9, 20), 200)
    _expense(db, business, date(2026, 9, 5), 100)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    assert value(api, business, "clients_seen", "2026-09-01") == "3"
    assert value(api, business, "sales_per_appointment", "2026-09-01") == "120.00"
    assert value(api, business, "cost_per_appointment", "2026-09-01") == "20.00"
    assert value(api, business, "profit_per_appointment", "2026-09-01") == "100.00"


def test_a_shop_is_not_shown_the_per_appointment_or_client_figures(api, db, business):
    say(api, business, "products")
    _sale(db, business, date(2026, 9, 3), 100)
    codes = kpi_codes(api, business)
    assert not {"clients_seen", "lapsed_clients", "sales_per_appointment"} & codes


def test_the_new_service_figures_have_plain_words_and_no_cause_is_claimed():
    for code in (
        "clients_seen", "lapsed_clients", "sales_per_appointment", "cost_per_appointment",
        "profit_per_appointment",
    ):  # fmt: skip
        for say_ in (WORDS[code].up, WORDS[code].down):
            assert say_.says.strip() and say_.why.strip()
            assert "because" not in (say_.says + say_.why).lower()


def test_a_name_typed_in_with_hours_is_suggested_for_appointments_and_the_other_way_round(
    api, db, business
):
    work(db, business, date(2026, 9, 1), "Mia", "8")
    work(db, business, date(2026, 9, 2), "Mia", "8")
    book(db, business, date(2026, 9, 1), None)
    with scoped(db, business):
        db.add(Booking(booked_for=date(2026, 9, 3), service="Cut", status="completed", staff="Ben"))
        db.flush()
    res = api.get(f"{ORGS}/{business[0]}/bookings/choices", headers=business[2]["owner"])
    assert res.json()["staff"] == ["Mia", "Ben"]  # Mia is used most
