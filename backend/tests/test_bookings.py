# ruff: noqa: F811
"""Bookings: what became of each appointment, typed in by hand (client feedback 15/10/2026, §6)."""

from sqlalchemy import func, select

from app.models.identity import AuditLog
from tests.test_manual_records import business, call, create, scoped  # noqa: F401

BOOKING = {"booked_for": "2026-09-10", "service": "Haircut"}


def test_a_booking_is_typed_in_and_read_back(api, business):
    made = create(api, business, "bookings", {**BOOKING, "staff": "Ola", "notes": "Fade"})
    assert made["status"] == "booked" and made["source"] == "manual" and made["staff"] == "Ola"
    got = call(api, business, "GET", f"bookings/{made['id']}").json()
    assert got["service"] == "Haircut" and got["booked_for"] == "2026-09-10"


def test_a_booking_can_be_linked_to_a_customer_of_the_business(api, business):
    customer = create(api, business, "customers", {"name": "Jo"})
    assert (
        create(api, business, "bookings", {**BOOKING, "customer_id": customer["id"]})["customer_id"]
        == customer["id"]
    )


def test_the_customer_must_be_one_of_yours(api, business):
    theirs = create(api, business, "customers", {"name": "Sam"}, org=1, who="other")
    res = call(api, business, "POST", "bookings", {**BOOKING, "customer_id": theirs["id"]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_reference"


def test_a_cancellation_records_the_day_it_was_cancelled(api, business):
    made = create(
        api, business, "bookings",
        {**BOOKING, "status": "cancelled", "cancelled_on": "2026-09-09"},
    )  # fmt: skip
    assert made["status"] == "cancelled" and made["cancelled_on"] == "2026-09-09"


def test_a_booking_cannot_be_cancelled_after_its_day_or_unless_cancelled(api, business):
    late = {**BOOKING, "status": "cancelled", "cancelled_on": "2026-09-11"}
    assert call(api, business, "POST", "bookings", late).status_code == 422
    kept = {**BOOKING, "status": "completed", "cancelled_on": "2026-09-09"}
    assert call(api, business, "POST", "bookings", kept).status_code == 422


def test_bad_bookings_are_refused(api, business):
    for bad in (
        {"service": "Haircut"},
        {**BOOKING, "service": "  "},
        {**BOOKING, "status": "done"},
        {**BOOKING, "price": 5},
        {**BOOKING, "booked_for": "10/09/2026"},
    ):
        assert call(api, business, "POST", "bookings", bad).status_code == 422, bad


def test_a_booking_can_be_changed_to_no_show_and_back(api, business):
    made = create(api, business, "bookings", BOOKING)
    res = call(api, business, "PATCH", f"bookings/{made['id']}", {"status": "no_show"})
    assert res.json()["status"] == "no_show"
    res = call(api, business, "PATCH", f"bookings/{made['id']}", {"staff": None, "notes": "x"})
    assert res.json()["staff"] is None and res.json()["notes"] == "x"


def test_changing_a_cancelled_booking_to_completed_forgets_the_cancel_day(api, business):
    made = create(
        api, business, "bookings",
        {**BOOKING, "status": "cancelled", "cancelled_on": "2026-09-09"},
    )  # fmt: skip
    res = call(api, business, "PATCH", f"bookings/{made['id']}", {"status": "completed"})
    assert res.json()["status"] == "completed" and res.json()["cancelled_on"] is None


def test_moving_a_booking_before_its_cancel_day_is_refused(api, business):
    made = create(
        api, business, "bookings",
        {**BOOKING, "status": "cancelled", "cancelled_on": "2026-09-09"},
    )  # fmt: skip
    res = call(api, business, "PATCH", f"bookings/{made['id']}", {"booked_for": "2026-09-01"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "cancellation_does_not_fit"


def test_listing_filters_and_puts_the_latest_day_first(api, business):
    create(api, business, "bookings", {**BOOKING, "booked_for": "2026-09-01"})
    create(
        api,
        business,
        "bookings",
        {"booked_for": "2026-09-20", "service": "Colour", "status": "completed"},
    )  # noqa: E501
    create(
        api,
        business,
        "bookings",
        {"booked_for": "2026-09-10", "service": "Colour", "status": "no_show"},
    )  # noqa: E501
    page = call(api, business, "GET", "bookings").json()
    assert [b["booked_for"] for b in page["items"]] == ["2026-09-20", "2026-09-10", "2026-09-01"]
    assert call(api, business, "GET", "bookings", status="no_show").json()["total"] == 1
    assert call(api, business, "GET", "bookings", q="colo").json()["total"] == 2
    assert (
        call(api, business, "GET", "bookings", date_from="2026-09-05", date_to="2026-09-15").json()[
            "total"
        ]
        == 1
    )  # noqa: E501
    assert call(api, business, "GET", "bookings", status="done").status_code == 422


def test_a_booking_can_be_deleted_and_the_change_is_recorded(api, db, business):
    made = create(api, business, "bookings", BOOKING)
    assert call(api, business, "DELETE", f"bookings/{made['id']}").status_code == 204
    assert call(api, business, "GET", f"bookings/{made['id']}").status_code == 404
    with scoped(db, business):
        kinds = db.scalars(
            select(func.count()).select_from(AuditLog).where(AuditLog.target_type == "bookings")
        ).one()
    assert kinds == 2  # created, deleted


def test_a_customer_with_bookings_cannot_be_deleted(api, business):
    customer = create(api, business, "customers", {"name": "Jo"})
    create(api, business, "bookings", {**BOOKING, "customer_id": customer["id"]})
    res = call(api, business, "DELETE", f"customers/{customer['id']}")
    assert res.status_code == 409


def test_a_viewer_cannot_see_or_change_bookings(api, business):
    made = create(api, business, "bookings", BOOKING)
    assert call(api, business, "GET", "bookings", who="viewer").status_code == 403
    assert call(api, business, "POST", "bookings", BOOKING, who="viewer").status_code == 403
    assert call(api, business, "DELETE", f"bookings/{made['id']}", who="viewer").status_code == 403


def test_each_business_has_its_own_bookings(api, business):
    made = create(api, business, "bookings", BOOKING)
    assert call(api, business, "GET", "bookings", org=1, who="other").json()["total"] == 0
    assert (
        call(api, business, "GET", f"bookings/{made['id']}", org=1, who="other").status_code == 404
    )
