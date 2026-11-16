# ruff: noqa: E501, F811
"""Importing the hours the team worked from a file."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.data import StaffHours
from app.services.import_fields import DATASET_FIELDS, REQUIRED, suggest_mapping
from app.services.import_rows import parse_row
from tests.test_import_run import checked, count, run, scoped, undo

D = Decimal
MAP = {"worked_on": "Day", "staff": "Who", "hours": "Hours", "reference": "Ref"}
HEADER = "Day,Who,Hours,Ref"


def how_many(db, business):
    with scoped(db, business):
        return count(db, StaffHours)


def file(*rows):
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def parse(row, mapping=None, today=date(2026, 10, 1)):
    raw = dict(zip(HEADER.split(","), row.split(","), strict=False))
    return parse_row("staff_hours", raw, mapping or MAP, {}, today=today)


def test_a_good_row_is_read_whole():
    out = parse("10/09/2026,Mia,7.5,H1")
    assert out.ok
    assert out.values["worked_on"] == date(2026, 9, 10)
    assert out.values["staff"] == "Mia" and out.values["hours"] == D("7.50")
    assert out.key == ("ref", "H1")


def test_hours_are_read_however_they_are_written():
    for written, hours in {
        "8": "8.00", "7.5": "7.50", "7:30": "7.50", "7h 30": "7.50",
        "7h30m": "7.50", "7.5 hours": "7.50", "450 min": "7.50", "2 hrs": "2.00",
    }.items():  # fmt: skip
        out = parse(f"10/09/2026,Mia,{written},")
        assert out.ok, (written, out.errors)
        assert out.values["hours"] == D(hours), written


def test_hours_that_make_no_sense_are_refused_in_plain_words():
    for bad in ("lots", "0", "-3", "25", "24:30"):
        out = parse(f"10/09/2026,Mia,{bad},")
        assert not out.ok, bad
        assert "hours" in out.errors[0]["message"], bad


def test_the_day_the_name_and_the_hours_are_needed():
    assert not parse(",Mia,8,").ok
    assert not parse("10/09/2026,,8,").ok
    assert not parse("10/09/2026,Mia,,").ok


def test_the_fields_and_what_is_required_are_known():
    assert {f.key for f in DATASET_FIELDS["staff_hours"]} == {
        "worked_on", "staff", "hours", "notes", "reference",
    }  # fmt: skip
    assert REQUIRED["staff_hours"] == ("worked_on", "staff", "hours")


def test_common_headings_are_matched_for_the_person():
    found = suggest_mapping("staff_hours", ["Shift date", "Employee", "Hours worked"])
    assert found == {"worked_on": "Shift date", "staff": "Employee", "hours": "Hours worked"}


def test_with_no_reference_the_same_person_day_and_hours_are_one_row():
    a = parse("10/09/2026,Mia,8,", {k: v for k, v in MAP.items() if k != "reference"})
    b = parse("10/09/2026,mia,8,", {k: v for k, v in MAP.items() if k != "reference"})
    c = parse("10/09/2026,Mia,4,", {k: v for k, v in MAP.items() if k != "reference"})
    assert a.key == b.key and a.key != c.key


# --- the whole import ---------------------------------------------------------------------------

ROWS = ("10/09/2026,Mia,8,H1", "10/09/2026,Ben,6.5,H2", "11/09/2026,Mia,7h 30,H3")


def test_importing_hours_creates_them(api, db, business):
    res = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "staff_hours"))
    assert res.status_code == 200, res.text
    assert res.json()["created"] == {"staff_hours": 3}
    with scoped(db, business):
        rows = db.scalars(select(StaffHours).order_by(StaffHours.worked_on, StaffHours.staff)).all()
        assert [(r.staff, r.hours, r.source, r.source_ref) for r in rows] == [
            ("Ben", D("6.50"), "csv", "H2"),
            ("Mia", D("8.00"), "csv", "H1"),
            ("Mia", D("7.50"), "csv", "H3"),
        ]


def test_uploading_the_same_file_again_adds_nothing(api, db, business):
    run(api, business, checked(api, business, file(*ROWS), MAP, {}, "staff_hours"))
    again = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "staff_hours"))
    assert again.status_code in (200, 409), again.text
    assert how_many(db, business) == 3


def test_undoing_an_import_removes_the_hours_it_added_and_leaves_those_typed_in(api, db, business):
    with scoped(db, business):
        db.add(StaffHours(worked_on=date(2026, 9, 1), staff="Mia", hours=D("8")))
        db.flush()
    imported = checked(api, business, file(*ROWS), MAP, {}, "staff_hours")
    run(api, business, imported)
    assert how_many(db, business) == 4
    undo(api, business, imported)
    assert how_many(db, business) == 1
