# ruff: noqa: E501
"""Which services people book, and where they do not come back (client feedback §4 example E)."""

from datetime import date, timedelta

import pytest

from app.models.data import Booking
from tests.test_booking_figures import book
from tests.test_kpi_customers_stock import customer
from tests.test_manual_records import call  # noqa: F401  (the business fixture is in conftest)

ORGS = "/api/v1/organizations"
LAST = date(2026, 9, 28)


def services(api, business, who="owner"):
    res = api.get(f"{ORGS}/{business[0]}/kpis/services", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def salon(db, business):
    """Cuts: 6 people kept one, 5 of them have another booked (83%). Colour: 6 people kept one, 1
    has another (17%, weak). Plus 2 cancelled cuts, 1 missed colour, and an old booking outside the window."""
    people = [customer(db, business, f"P{i}") for i in range(12)]
    d = lambda n: LAST - timedelta(days=n)  # noqa: E731
    for i in range(6):
        book(db, business, d(30 + i), people[i], service="Cut")
    for i in range(5):  # five of the cut people come again
        book(db, business, LAST + timedelta(days=7), people[i], status="booked", service="Cut")
    for i in range(6, 12):
        book(db, business, d(20 + i), people[i], service="Colour")
    book(db, business, LAST + timedelta(days=9), people[6], status="booked", service="Colour")
    book(db, business, d(5), people[0], status="cancelled", cancelled_on=d(8), service="Cut")
    book(db, business, d(4), people[1], status="cancelled", cancelled_on=d(4), service="Cut")
    book(db, business, d(3), people[7], status="no_show", service="Colour")
    book(db, business, d(200), people[2], service="Beard")  # long ago: not in the last three months
    book(db, business, LAST, people[3], service="Cut")  # sets the end of the window
    return business


def test_services_are_listed_most_booked_first_with_what_became_of_them(api, salon):
    body = services(api, salon)
    assert [r["service"] for r in body["rows"]] == ["Cut", "Colour"]
    cut, colour = body["rows"]
    assert (cut["kept"], cut["cancelled"], cut["missed"]) == (7, 2, 0)
    assert (colour["kept"], colour["cancelled"], colour["missed"]) == (6, 0, 1)
    assert body["kept"] == 13 and body["period_to"] == LAST.isoformat()


def test_the_old_booking_is_outside_the_window(api, salon):
    assert "Beard" not in [r["service"] for r in services(api, salon)["rows"]]


def test_rebooking_is_counted_by_service_and_overall(api, salon):
    body = services(api, salon)
    cut, colour = body["rows"]
    assert cut["customers"] == 6 and cut["rebooked_pct"] == "83.3"
    assert colour["customers"] == 6 and colour["rebooked_pct"] == "16.7"
    assert body["rebooked_pct"] == "50.0"  # 6 of 12


def test_the_notes_name_the_favourite_and_the_service_nobody_returns_for(api, salon):
    notes = services(api, salon)["notes"]
    assert notes[0] == "Cut is your most booked service: 7 kept in the last three months."
    assert any(
        "Few people who have Colour book again (16.7%, against 50.0% across all your services)" in n
        for n in notes
    )
    assert not any("Cut book again" in n for n in notes)


def test_a_handful_of_customers_is_not_enough_to_call_a_service_weak(db, api, business):
    people = [customer(db, business, f"Q{i}") for i in range(3)]
    for p in people:
        book(db, business, LAST - timedelta(days=10), p, service="Facial")
    assert not any("Facial" in n for n in services(api, business)["notes"][1:])


def test_a_business_with_no_appointments_gets_an_empty_answer(api, business):
    assert services(api, business) == {
        "period_from": None,
        "period_to": None,
        "kept": 0,
        "rebooked_pct": None,
        "rows": [],
        "notes": [],
    }


def test_the_services_and_staff_already_used_are_offered_most_used_first(api, db, business):
    for service, staff in (("Cut", "Ola"), ("Cut", "Ola"), ("Colour", "Ife"), ("Beard", None)):
        with __import__("tests.test_health", fromlist=["scoped"]).scoped(db, business):
            db.add(Booking(booked_for=LAST, service=service, status="completed", staff=staff))
            db.flush()
    res = api.get(f"{ORGS}/{business[0]}/bookings/choices", headers=business[2]["owner"])
    assert res.status_code == 200 and res.json() == {
        "services": ["Cut", "Beard", "Colour"],
        "staff": ["Ola", "Ife"],
    }


def test_a_viewer_sees_the_services_but_not_the_choices_list(api, salon):
    assert services(api, salon, "viewer")["rows"]
    assert (
        api.get(f"{ORGS}/{salon[0]}/bookings/choices", headers=salon[2]["viewer"]).status_code
        == 403
    )


def test_each_business_sees_only_its_own_services(api, salon):
    other = api.get(f"{ORGS}/{salon[1]}/kpis/services", headers=salon[2]["other"]).json()
    assert other["rows"] == []
