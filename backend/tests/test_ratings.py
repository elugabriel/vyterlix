# ruff: noqa: E501, F811
"""How customers rated the appointments (customer satisfaction, client feedback section 6)."""

from datetime import date

import pytest

from app.plain.figures import WORDS
from app.services.import_rows import parse_row
from tests.test_booking_figures import book
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped
from tests.test_manual_records import call, create

CODES = {"happy_ratings_pct", "unhappy_ratings_pct"}
BOOKING = {"booked_for": "2026-09-10", "service": "Cut", "status": "completed"}


def test_an_appointment_that_went_ahead_can_be_rated(api, business):
    made = create(api, business, "bookings", {**BOOKING, "rating": 5})
    assert made["rating"] == 5
    assert call(api, business, "GET", f"bookings/{made['id']}").json()["rating"] == 5


@pytest.mark.parametrize("rating", [0, 6, -1, 2.5, "great"])
def test_only_whole_ratings_from_one_to_five_are_accepted(api, business, rating):
    assert call(api, business, "POST", "bookings", {**BOOKING, "rating": rating}).status_code == 422


@pytest.mark.parametrize("status", ["booked", "cancelled", "no_show"])
def test_an_appointment_that_did_not_go_ahead_cannot_be_rated(api, business, status):
    assert (
        call(
            api, business, "POST", "bookings", {**BOOKING, "status": status, "rating": 4}
        ).status_code
        == 422
    )


def test_a_rating_can_be_added_changed_and_removed_and_goes_when_it_stops_being_completed(
    api, business
):
    made = create(api, business, "bookings", BOOKING)
    assert made["rating"] is None
    assert (
        call(api, business, "PATCH", f"bookings/{made['id']}", {"rating": 2}).json()["rating"] == 2
    )
    assert (
        call(api, business, "PATCH", f"bookings/{made['id']}", {"rating": 5}).json()["rating"] == 5
    )
    assert (
        call(api, business, "PATCH", f"bookings/{made['id']}", {"rating": None}).json()["rating"]
        is None
    )
    call(api, business, "PATCH", f"bookings/{made['id']}", {"rating": 4})
    moved = call(api, business, "PATCH", f"bookings/{made['id']}", {"status": "no_show"}).json()
    assert moved["rating"] is None


# --- from a file ----------------------------------------------------------------------------------


def parse(row, status="completed"):
    header = "Date,Service,Outcome,Rating"
    return parse_row(
        "bookings",
        dict(zip(header.split(","), row.split(","), strict=False)),
        {"booked_for": "Date", "service": "Service", "status": "Outcome", "rating": "Rating"},
        {},
        today=date(2026, 10, 1),
    )


@pytest.mark.parametrize(
    ("text", "number"), [("5", 5), ("4.0", 4), ("3/5", 3), ("2 stars", 2), ("1 star", 1)]
)
def test_ratings_are_read_in_the_ways_people_write_them(text, number):
    assert parse(f"10/09/2026,Cut,completed,{text}").values["rating"] == number


@pytest.mark.parametrize("text", ["0", "6", "4.5", "good", "10/10"])
def test_a_silly_rating_is_refused(text):
    out = parse(f"10/09/2026,Cut,completed,{text}")
    assert not out.ok and out.errors[0]["code"] == "invalid_rating"


def test_a_rating_on_a_missed_appointment_is_refused():
    out = parse("10/09/2026,Cut,no show,4")
    assert not out.ok and out.errors[0]["code"] == "not_completed"


# --- the figures, worked out by hand ---------------------------------------------------------------


@pytest.fixture
def rated(db, business):
    """September: ten rated appointments: 5, 5, 5, 4, 4, 3, 2, 1, 1, 1 (five happy, three unhappy), plus one not rated."""
    for day, rating in enumerate([5, 5, 5, 4, 4, 3, 2, 1, 1, 1], start=1):
        with scoped(db, business):
            from app.models.data import Booking

            db.add(
                Booking(
                    booked_for=date(2026, 9, day), service="Cut", status="completed", rating=rating
                )
            )
            db.flush()
    book(db, business, date(2026, 9, 20))  # kept but not rated
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def test_the_share_who_were_happy_and_unhappy(api, rated):
    assert value(api, rated, "happy_ratings_pct", "2026-09-01") == "50.00"  # 5 of the 10 rated
    assert value(api, rated, "unhappy_ratings_pct", "2026-09-01") == "40.00"  # 4 of 10: 2, 1, 1, 1


def codes(api, business):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"])
    return {k["code"] for k in res.json()["kpis"]}


def test_the_figures_only_appear_once_something_has_been_rated(api, db, business):
    book(db, business, date(2026, 9, 1))  # an appointment, but nobody rated it
    assert not CODES & codes(api, business)
    with scoped(db, business):
        from sqlalchemy import update

        from app.models.data import Booking

        db.execute(update(Booking).values(rating=5))
        db.flush()
    assert CODES <= codes(api, business)


def test_the_words_and_the_suggestion(api, db, business):
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert (
                say.says.strip()
                and say.why.strip()
                and "because" not in (say.says + say.why).lower()
            )
    from tests.test_detection import Values
    from tests.test_detection import run as detect
    from tests.test_recommendations import event_for, recommend

    values = Values(db, business)
    values.put("unhappy_ratings_pct", date(2026, 2, 1), 5, None)
    values.put("unhappy_ratings_pct", date(2026, 3, 1), 30, 5)
    detect(db, business)
    body = recommend(api, business, event_for(api, business, "unhappy_ratings_pct")).json()
    assert body["options"][0]["title"] == "Call the customers who gave a low rating"
