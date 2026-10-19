# ruff: noqa: E501, F811
"""Importing leads, proposals and renewals from a file."""

from datetime import date
from decimal import Decimal as D

from sqlalchemy import select

from app.models.data import Customer, Opportunity
from app.services.import_fields import DATASET_FIELDS, REQUIRED, suggest_mapping
from app.services.import_rows import parse_row
from tests.test_import_run import checked, count, run, scoped, undo

MAP = {
    "name": "Deal",
    "stage": "Stage",
    "kind": "Type",
    "value": "Value",
    "created_on": "Created",
    "proposal_on": "Proposal sent",
    "closed_on": "Closed",
    "customer_name": "Client",
    "customer_email": "Email",
    "reference": "Ref",
}
HEADER = "Deal,Stage,Type,Value,Created,Proposal sent,Closed,Client,Email,Ref"
TODAY = date(2026, 10, 1)


def file(*rows):
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def parse(row, mapping=None):
    raw = dict(zip(HEADER.split(","), row.split(","), strict=False))
    return parse_row("opportunities", raw, mapping or MAP, {}, today=TODAY)


def test_a_good_row_is_read_whole():
    out = parse(
        "Website for Acme,won,new,4000,01/09/2026,05/09/2026,20/09/2026,Acme,hi@acme.co.uk,L1"
    )
    v = out.values
    assert (
        out.ok
        and v["stage"] == "won"
        and v["value"] == D("4000")
        and v["closed_on"] == date(2026, 9, 20)
    )
    assert out.key == ("ref", "L1")


def test_everyday_words_for_the_stage_and_the_kind_are_understood():
    for word, stage in {
        "Enquiry": "lead",
        "Quote": "proposal",
        "Closed won": "won",
        "Signed": "won",
        "Declined": "lost",
        "Prospect": "lead",
    }.items():
        assert parse(f"A,{word},,,01/09/2026,,,,,").values["stage"] == stage, word
    for word, kind in {"Renewing": "renewal", "New work": "new", "Existing": "renewal"}.items():
        assert parse(f"A,lead,{word},,01/09/2026,,,,,").values["kind"] == kind, word


def test_an_unknown_stage_or_kind_is_refused_plainly():
    assert "isn't a stage" in parse("A,maybe,,,01/09/2026,,,,,").errors[0]["message"]
    assert (
        "new work or a renewal" in parse("A,lead,sometimes,,01/09/2026,,,,,").errors[0]["message"]
    )


def test_without_a_stage_the_dates_decide_between_lead_and_proposal():
    mapping = {k: v for k, v in MAP.items() if k != "stage"}
    assert parse("A,,,,01/09/2026,,,,,", mapping).values["stage"] == "lead"
    assert parse("A,,,,01/09/2026,05/09/2026,,,,", mapping).values["stage"] == "proposal"


def test_a_decision_day_without_a_stage_is_refused_rather_than_guessed():
    mapping = {k: v for k, v in MAP.items() if k != "stage"}
    out = parse("A,,,,01/09/2026,,20/09/2026,,,", mapping)
    assert not out.ok and out.errors[0]["code"] == "stage_needed"


def test_won_and_lost_get_a_decision_day_and_a_lead_has_none():
    assert parse("A,won,,,01/09/2026,,,,,").values["closed_on"] == TODAY
    assert parse("A,lead,,,01/09/2026,,20/09/2026,,,").values["closed_on"] is None


def test_days_must_come_in_order():
    assert parse("A,won,,,10/09/2026,,01/09/2026,,,").errors[0]["code"] == "before_the_lead"
    assert (
        parse("A,won,,,01/08/2026,10/09/2026,01/09/2026,,,").errors[0]["code"]
        == "before_the_proposal"
    )


def test_the_worth_cannot_be_negative_and_a_name_is_needed():
    assert not parse("A,lead,,-5,01/09/2026,,,,,").ok
    assert not parse(",lead,,,01/09/2026,,,,,").ok


def test_the_fields_and_headings_are_known():
    assert REQUIRED["opportunities"] == ("name",) and {
        f.key for f in DATASET_FIELDS["opportunities"]
    } >= {"name", "stage", "kind", "value"}
    found = suggest_mapping("opportunities", ["Deal value", "Stage", "Client", "Date closed"])
    assert found == {
        "value": "Deal value",
        "stage": "Stage",
        "customer_name": "Client",
        "closed_on": "Date closed",
    }


ROWS = (
    "Website for Acme,won,new,4000,01/09/2026,05/09/2026,20/09/2026,Acme,hi@acme.co.uk,L1",
    "Acme yearly contract,lost,renewal,1200,02/09/2026,06/09/2026,21/09/2026,Acme,hi@acme.co.uk,L2",
    "Rebrand for Bold,proposal,new,2500,03/09/2026,07/09/2026,,Bold Ltd,,L3",
)


def test_importing_creates_them_and_their_clients(api, db, business):
    res = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "opportunities"))
    assert res.status_code == 200, res.text
    assert res.json()["created"] == {"customers": 2, "opportunities": 3}
    with scoped(db, business):
        rows = db.scalars(select(Opportunity).order_by(Opportunity.created_on)).all()
        assert [(o.name, o.stage, o.kind, o.source_ref) for o in rows] == [
            ("Website for Acme", "won", "new", "L1"),
            ("Acme yearly contract", "lost", "renewal", "L2"),
            ("Rebrand for Bold", "proposal", "new", "L3"),
        ]
        assert rows[0].customer_id == rows[1].customer_id and rows[2].closed_on is None
        assert {c.name for c in db.scalars(select(Customer))} == {"Acme", "Bold Ltd"}


def test_the_same_file_again_adds_nothing(api, db, business):
    run(api, business, checked(api, business, file(*ROWS), MAP, {}, "opportunities"))
    again = run(api, business, checked(api, business, file(*ROWS), MAP, {}, "opportunities"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_import"
    with scoped(db, business):
        assert count(db, Opportunity) == 3


def test_undoing_removes_the_leads_and_the_clients_it_made(api, db, business):
    import_id = checked(api, business, file(*ROWS), MAP, {}, "opportunities")
    run(api, business, import_id)
    assert undo(api, business, import_id).status_code == 200
    with scoped(db, business):
        assert count(db, Opportunity) == 0 and count(db, Customer) == 0


def test_leads_typed_in_by_hand_survive_an_undo(api, db, business):
    with scoped(db, business):
        db.add(Opportunity(name="Mine", created_on=date(2026, 9, 1)))
        db.flush()
    import_id = checked(api, business, file(*ROWS), MAP, {}, "opportunities")
    run(api, business, import_id)
    undo(api, business, import_id)
    with scoped(db, business):
        assert count(db, Opportunity) == 1
