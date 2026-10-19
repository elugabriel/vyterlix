# ruff: noqa: E501, F811
"""Regular clients (the repeat-contract side) and the team's workload (the staff side) of client
feedback section 6."""

from datetime import date, timedelta

import pytest

from app.models.data import Booking
from app.plain.figures import WORDS
from tests.test_booking_figures import book
from tests.test_kpi_customers_stock import customer, value
from tests.test_kpi_engine import run, scoped

ORGS = "/api/v1/organizations"
LAST = date(2026, 9, 28)


# --- regular clients ----------------------------------------------------------------------------


def test_a_regular_is_someone_with_three_or_more_visits_in_three_months(db, api, business):
    ana, ben, cat = (customer(db, business, n) for n in ("Ana", "Ben", "Cat"))
    for day in (date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5)):
        book(db, business, day, ana)  # three, within 90 days of the end of September
    book(db, business, date(2026, 9, 6), ben)
    book(db, business, date(2026, 9, 7), ben)  # only two
    for day in (date(2026, 5, 1), date(2026, 8, 1), date(2026, 9, 1)):
        book(db, business, day, cat)  # one is older than 90 days before the end of September
    book(db, business, date(2026, 9, 9), ana, status="no_show")  # a missed one does not count
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    assert value(api, business, "regular_clients", "2026-09-01") == "1"


def test_the_count_moves_as_the_window_moves(db, api, business):
    ana = customer(db, business, "Ana")
    for day in (date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5)):
        book(db, business, day, ana)
    run(db, business, first=date(2026, 9, 1), today=date(2026, 12, 15))
    assert value(api, business, "regular_clients", "2026-09-01") == "1"
    assert (
        value(api, business, "regular_clients", "2026-11-01") == "0"
    )  # by the end of November the July one is out


def test_the_words_claim_no_cause():
    for say in (WORDS["regular_clients"].up, WORDS["regular_clients"].down):
        assert (
            say.says.strip() and say.why.strip() and "because" not in (say.says + say.why).lower()
        )


def test_a_fall_in_regulars_suggests_winning_them_back(api, db, business):
    from tests.test_detection import Values
    from tests.test_detection import run as detect
    from tests.test_recommendations import event_for, recommend

    values = Values(db, business)
    values.put("regular_clients", date(2026, 2, 1), 30, None)
    values.put("regular_clients", date(2026, 3, 1), 12, 30)
    detect(db, business)
    body = recommend(api, business, event_for(api, business, "regular_clients")).json()
    assert body["options"][0]["title"] == "Win back the customers who stopped coming"


# --- the team ------------------------------------------------------------------------------------


def team(api, business, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/kpis/team", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def add(db, business, staff, status="completed", rating=None, days=10):
    with scoped(db, business):
        db.add(
            Booking(
                booked_for=LAST - timedelta(days=days),
                service="Cut",
                status=status,
                staff=staff,
                rating=rating,
            )
        )
        db.flush()


@pytest.fixture
def salon(db, business):
    """Ola kept 7 (rated 5, 4, 5), cancelled 1. Ife kept 3 (rated 2), missed 1. A kept one with nobody named."""
    for i in range(7):
        add(db, business, "Ola", rating=[5, 4, 5][i] if i < 3 else None, days=i)
    add(db, business, "Ola", "cancelled", days=8)
    for i in range(3):
        add(db, business, "Ife", rating=2 if i == 0 else None, days=i + 10)
    add(db, business, "Ife", "no_show", days=14)
    add(db, business, None, days=3)
    add(db, business, "Ife", "booked", days=-5)  # still to come
    return business


def test_each_person_with_what_they_did(api, salon):
    body = team(api, salon)
    ola, ife = body["members"]
    assert (ola["staff"], ola["kept"], ola["cancelled"], ola["missed"]) == ("Ola", 7, 1, 0)
    assert (ife["staff"], ife["kept"], ife["cancelled"], ife["missed"]) == ("Ife", 3, 0, 1)
    assert body["kept"] == 10  # an appointment with nobody named is not counted against anyone
    assert (ola["share_pct"], ife["share_pct"]) == ("70.0", "30.0")


def test_the_average_rating_is_of_the_appointments_that_were_rated(api, salon):
    ola, ife = team(api, salon)["members"]
    assert ola["average_rating"] == "4.7" and ife["average_rating"] == "2.0"


def test_it_says_when_one_person_carries_most_and_that_this_is_not_capacity(api, salon):
    notes = team(api, salon)["notes"]
    assert notes[0].startswith("Ola did 70.0% of the appointments")
    assert "not who has room for more" in notes[-1]


def test_a_single_person_is_not_told_to_share_the_work(db, api, business):
    for i in range(5):
        add(db, business, "Ola", days=i)
    notes = team(api, business)["notes"]
    assert not any("did 100" in n for n in notes) and "not who has room for more" in notes[-1]


def test_no_appointments_means_an_empty_answer(api, business):
    assert team(api, business) == {
        "period_from": None,
        "period_to": None,
        "kept": 0,
        "members": [],
        "notes": [],
    }


def test_a_viewer_can_see_it_and_another_business_cannot_see_the_data(api, salon):
    assert team(api, salon, "viewer")["kept"] == 10
    assert team(api, salon, "other", org=1)["members"] == []
