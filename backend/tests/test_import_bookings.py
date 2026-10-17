# ruff: noqa: E501, F811
"""Importing appointments from a file (client feedback §6: bring in a salon's or a repair firm's bookings)."""

from datetime import date

from sqlalchemy import select

from app.models.data import Booking, Customer
from app.services.import_fields import DATASET_FIELDS, REQUIRED, suggest_mapping
from app.services.import_rows import parse_row
from tests.test_import_run import checked, count, run, scoped, undo

MAP = {
    "booked_for": "Date",
    "service": "Service",
    "status": "Outcome",
    "customer_name": "Client",
    "customer_email": "Email",
    "staff": "Staff",
    "cancelled_on": "Cancelled on",
    "reference": "Ref",
}
HEADER = "Date,Service,Outcome,Client,Email,Staff,Cancelled on,Ref"


def file(*rows):
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def parse(row, mapping=None, today=date(2026, 10, 1)):
    raw = dict(zip(HEADER.split(","), row.split(","), strict=False))
    return parse_row("bookings", raw, mapping or MAP, {}, today=today)


# --- reading one row ----------------------------------------------------------------------------


def test_a_good_row_is_read_whole():
    out = parse("10/09/2026,Cut,completed,Jo Baker,jo@acme.co.uk,Ola,,B1")
    assert out.ok and out.values["status"] == "completed" and out.values["service"] == "Cut"
    assert (
        out.values["booked_for"] == date(2026, 9, 10)
        and out.values["customer_email"] == "jo@acme.co.uk"
    )
    assert out.key == ("ref", "B1")


def test_everyday_words_for_what_happened_are_understood():
    words = {
        "Attended": "completed",
        "DNA": "no_show",
        "Did not attend": "no_show",
        "Cancelled": "cancelled",
        "Canceled": "cancelled",
        "Confirmed": "booked",
        "No-show": "no_show",
    }
    for word, status in words.items():
        out = parse(f"10/09/2026,Cut,{word},,,,,")
        assert out.values["status"] == status, word


def test_an_unknown_outcome_is_refused_in_plain_words():
    out = parse("10/09/2026,Cut,maybe,,,,,")
    assert (
        not out.ok
        and "isn't something that can happen to an appointment" in out.errors[0]["message"]
    )


def test_with_no_outcome_column_the_day_decides():
    mapping = {k: v for k, v in MAP.items() if k != "status"}
    assert parse("10/09/2026,Cut,,,,,,", mapping).values["status"] == "completed"  # in the past
    assert parse("10/11/2026,Cut,,,,,,", mapping).values["status"] == "booked"  # still to come


def test_the_day_and_the_service_are_needed():
    assert not parse(",Cut,completed,,,,,").ok
    assert not parse("10/09/2026,,completed,,,,,").ok


def test_a_cancellation_must_fit():
    assert not parse(
        "10/09/2026,Cut,completed,,,,09/09/2026,"
    ).ok  # a day cancelled, but not cancelled
    assert not parse("10/09/2026,Cut,cancelled,,,,11/09/2026,").ok  # cancelled after its own day
    assert parse("10/09/2026,Cut,cancelled,,,,09/09/2026,").ok


def test_the_fields_and_what_is_required_are_known():
    assert {f.key for f in DATASET_FIELDS["bookings"]} >= {
        "booked_for",
        "service",
        "status",
        "customer_name",
    }
    assert REQUIRED["bookings"] == ("booked_for", "service")


def test_common_headings_are_matched_for_the_person():
    found = suggest_mapping(
        "bookings", ["Appointment date", "Treatment", "Client name", "Stylist", "Status"]
    )
    assert found == {
        "booked_for": "Appointment date",
        "service": "Treatment",
        "customer_name": "Client name",
        "staff": "Stylist",
        "status": "Status",
    }


# --- the whole import ---------------------------------------------------------------------------

ROWS = (
    "10/09/2026,Cut,completed,Jo Baker,jo@acme.co.uk,Ola,,B1",
    "11/09/2026,Colour,no show,Sam Lee,,Ife,,B2",
    "12/09/2026,Cut,cancelled,Jo Baker,jo@acme.co.uk,Ola,11/09/2026,B3",
)


def test_importing_appointments_creates_them_and_their_customers(api, db, business):
    res = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "bookings"))
    assert res.status_code == 200, res.text
    assert res.json()["created"] == {"customers": 2, "bookings": 3}
    with scoped(db, business):
        rows = db.scalars(select(Booking).order_by(Booking.booked_for)).all()
        assert [(b.service, b.status, b.source, b.source_ref) for b in rows] == [
            ("Cut", "completed", "csv", "B1"),
            ("Colour", "no_show", "csv", "B2"),
            ("Cut", "cancelled", "csv", "B3"),
        ]
        assert rows[2].cancelled_on == date(2026, 9, 11) and rows[0].staff == "Ola"
        assert (
            rows[0].customer_id == rows[2].customer_id
            and rows[0].customer_id != rows[1].customer_id
        )
        assert {c.name for c in db.scalars(select(Customer))} == {"Jo Baker", "Sam Lee"}


def test_a_customer_who_already_exists_is_not_made_twice(api, db, business):
    with scoped(db, business):
        db.add(Customer(name="Jo Baker", email="jo@acme.co.uk"))
        db.flush()
    run(api, business, checked(api, business, file(*ROWS), MAP, {}, "bookings"))
    with scoped(db, business):
        assert count(db, Customer) == 2


def test_uploading_the_same_file_again_adds_nothing(api, db, business):
    run(api, business, checked(api, business, file(*ROWS), MAP, {}, "bookings"))
    again = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "bookings"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_import"
    with scoped(db, business):
        assert count(db, Booking) == 3


def test_undoing_an_import_removes_the_appointments_and_the_customers_it_made(api, db, business):
    import_id = checked(api, business, file(*ROWS), MAP, {}, "bookings")
    run(api, business, import_id)
    assert undo(api, business, import_id).status_code == 200
    with scoped(db, business):
        assert count(db, Booking) == 0 and count(db, Customer) == 0


def test_appointments_made_by_hand_are_left_when_an_import_is_undone(api, db, business):
    with scoped(db, business):
        db.add(Booking(booked_for=date(2026, 9, 1), service="Cut", status="completed"))
        db.flush()
    import_id = checked(api, business, file(*ROWS), MAP, {}, "bookings")
    run(api, business, import_id)
    undo(api, business, import_id)
    with scoped(db, business):
        assert count(db, Booking) == 1
