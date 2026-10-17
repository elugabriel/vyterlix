# ruff: noqa: E501, F811
"""Leads, proposals and renewals, and the figures from them (the consultancy and agency side of
client feedback section 6)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.core.uk import today_uk
from app.models.data import Opportunity
from app.plain.figures import WORDS
from tests.test_kpi_customers_stock import value
from tests.test_kpi_engine import run, scoped
from tests.test_manual_records import call, create

CODES = {"new_leads", "proposals_sent", "win_rate_pct", "work_won", "renewal_rate_pct"}


# --- typing them in --------------------------------------------------------------------------------


def test_a_lead_is_typed_in_and_read_back(api, business):
    made = create(api, business, "opportunities", {"name": "Website for Acme", "value": "4000"})
    assert (
        made["stage"] == "lead"
        and made["kind"] == "new"
        and made["created_on"] == today_uk().isoformat()
    )
    assert made["value"] == "4000.00" and made["proposal_on"] is None and made["closed_on"] is None
    assert (
        call(api, business, "GET", f"opportunities/{made['id']}").json()["name"]
        == "Website for Acme"
    )


def test_a_proposal_gets_the_day_it_was_sent(api, business):
    made = create(api, business, "opportunities", {"name": "Rebrand", "stage": "proposal"})
    assert made["proposal_on"] == today_uk().isoformat()


def test_won_and_lost_get_the_day_they_were_decided(api, business):
    won = create(api, business, "opportunities", {"name": "A", "stage": "won"})
    assert won["closed_on"] == today_uk().isoformat()
    lost = create(
        api,
        business,
        "opportunities",
        {"name": "B", "stage": "lost", "closed_on": "2026-09-01", "created_on": "2026-08-01"},
    )
    assert lost["closed_on"] == "2026-09-01"


def test_moving_a_lead_on_fills_in_the_days_and_moving_it_back_clears_them(api, business):
    made = create(api, business, "opportunities", {"name": "A", "created_on": "2026-08-01"})
    won = call(
        api,
        business,
        "PATCH",
        f"opportunities/{made['id']}",
        {"stage": "won", "closed_on": "2026-09-05"},
    ).json()
    assert won["stage"] == "won" and won["closed_on"] == "2026-09-05"
    back = call(api, business, "PATCH", f"opportunities/{made['id']}", {"stage": "lead"}).json()
    assert back["closed_on"] is None and back["proposal_on"] is None


def test_days_must_come_in_order(api, business):
    bad = {"name": "A", "created_on": "2026-09-10", "stage": "won", "closed_on": "2026-09-01"}
    res = call(api, business, "POST", "opportunities", bad)
    assert res.status_code == 422 and res.json()["error"]["code"] == "before_the_lead"
    res = call(
        api,
        business,
        "POST",
        "opportunities",
        {
            "name": "A",
            "created_on": "2026-08-01",
            "stage": "won",
            "proposal_on": "2026-09-10",
            "closed_on": "2026-09-01",
        },
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "before_the_proposal"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": " "},
        {"name": "A", "stage": "pending"},
        {"name": "A", "kind": "repeat"},
        {"name": "A", "value": "-5"},
        {"name": "A", "extra": 1},
    ],
)
def test_bad_ones_are_refused(api, business, body):
    assert call(api, business, "POST", "opportunities", body).status_code == 422


def test_it_can_be_linked_to_a_customer_of_the_business_only(api, business):
    mine = create(api, business, "customers", {"name": "Acme"})
    assert (
        create(api, business, "opportunities", {"name": "A", "customer_id": mine["id"]})[
            "customer_id"
        ]
        == mine["id"]
    )
    theirs = create(api, business, "customers", {"name": "Other"}, org=1, who="other")
    assert (
        call(
            api, business, "POST", "opportunities", {"name": "A", "customer_id": theirs["id"]}
        ).status_code
        == 422
    )


def test_listing_filters(api, business):
    create(api, business, "opportunities", {"name": "Alpha", "created_on": "2026-09-01"})
    create(
        api,
        business,
        "opportunities",
        {"name": "Beta", "stage": "won", "kind": "renewal", "created_on": "2026-09-05"},
    )
    page = call(api, business, "GET", "opportunities").json()
    assert [i["name"] for i in page["items"]] == ["Beta", "Alpha"]
    assert call(api, business, "GET", "opportunities", stage="won").json()["total"] == 1
    assert call(api, business, "GET", "opportunities", kind="renewal").json()["total"] == 1
    assert call(api, business, "GET", "opportunities", q="alp").json()["total"] == 1


def test_deleting_and_who_may_use_it(api, business):
    made = create(api, business, "opportunities", {"name": "A"})
    assert call(api, business, "GET", "opportunities", who="viewer").status_code == 403
    assert (
        call(api, business, "DELETE", f"opportunities/{made['id']}", who="viewer").status_code
        == 403
    )
    assert call(api, business, "DELETE", f"opportunities/{made['id']}").status_code == 204
    assert call(api, business, "GET", "opportunities", org=1, who="other").json()["total"] == 0


# --- the figures, worked out by hand ------------------------------------------------------------------


def lead(
    db, business, name, created, stage="lead", kind="new", value=None, proposal=None, closed=None
):
    with scoped(db, business):
        db.add(
            Opportunity(
                name=name,
                stage=stage,
                kind=kind,
                value=None if value is None else D(value),
                created_on=created,
                proposal_on=proposal,
                closed_on=closed,
            )
        )
        db.flush()


@pytest.fixture
def desk(db, business):
    """September 2026. Leads that came in: A, B, C, D, E (five). Proposals sent in September: B, C, D, E (four).
    Decided in September: B won £4,000, C won £1,000 (a renewal), D lost, E lost (a renewal). A is still a lead.
    F came in in August and was won on 3 September (£500)."""
    s = lambda d: date(2026, 9, d)  # noqa: E731
    lead(db, business, "A", s(1))
    lead(db, business, "B", s(2), "won", value=4000, proposal=s(5), closed=s(20))
    lead(db, business, "C", s(3), "won", "renewal", 1000, proposal=s(6), closed=s(21))
    lead(db, business, "D", s(4), "lost", proposal=s(7), closed=s(22))
    lead(db, business, "E", s(5), "lost", "renewal", proposal=s(8), closed=s(23))
    lead(
        db,
        business,
        "F",
        date(2026, 8, 20),
        "won",
        value=500,
        proposal=date(2026, 8, 25),
        closed=s(3),
    )
    run(db, business, first=date(2026, 9, 1), today=date(2026, 11, 15))
    return business


def sept(api, desk, code):
    return value(api, desk, code, "2026-09-01")


def test_leads_and_proposals_are_counted_in_the_month_they_happened(api, desk):
    assert sept(api, desk, "new_leads") == "5"
    assert sept(api, desk, "proposals_sent") == "4"


def test_the_win_rate_is_of_the_ones_decided(api, desk):
    # decided in September: F, B and C won, D and E lost. A is still being chased and does not count against it
    assert sept(api, desk, "win_rate_pct") == "60.00"


def test_the_value_of_the_work_won(api, desk):
    assert sept(api, desk, "work_won") == "5500.00"  # F 500, B 4,000, C 1,000


def test_the_renewal_rate_is_of_the_renewals_decided(api, desk):
    assert sept(api, desk, "renewal_rate_pct") == "50.00"  # C renewed, E did not


# --- who sees them ------------------------------------------------------------------------------------


def codes(api, business):
    res = api.get(f"/api/v1/organizations/{business[0]}/kpis", headers=business[2]["owner"])
    return {k["code"] for k in res.json()["kpis"]}


def test_a_business_that_does_not_quote_for_work_is_not_shown_them(api, business):
    assert not CODES & codes(api, business)


def test_once_there_is_a_lead_they_appear(api, db, business):
    lead(db, business, "A", date(2026, 9, 1))
    assert CODES <= codes(api, business)


def test_every_figure_has_plain_words_and_claims_no_cause():
    for code in CODES:
        for say in (WORDS[code].up, WORDS[code].down):
            assert (
                say.says.strip()
                and say.why.strip()
                and "because" not in (say.says + say.why).lower()
            )


# --- what we suggest ----------------------------------------------------------------------------------


def test_a_fall_in_the_win_rate_suggests_looking_back_at_lost_proposals(api, db, business):
    from tests.test_detection import Values
    from tests.test_detection import run as detect
    from tests.test_recommendations import event_for, recommend

    values = Values(db, business)
    values.put("win_rate_pct", date(2026, 2, 1), 60, None)
    values.put("win_rate_pct", date(2026, 3, 1), 30, 60)
    detect(db, business)
    body = recommend(api, business, event_for(api, business, "win_rate_pct")).json()
    assert (
        body["status"] == "open"
        and body["options"][0]["title"] == "Look back at the proposals you lost"
    )


def test_fewer_renewals_suggests_talking_to_clients_before_the_renewal(api, db, business):
    from tests.test_detection import Values
    from tests.test_detection import run as detect
    from tests.test_recommendations import event_for, recommend

    values = Values(db, business)
    values.put("renewal_rate_pct", date(2026, 2, 1), 90, None)
    values.put("renewal_rate_pct", date(2026, 3, 1), 55, 90)
    detect(db, business)
    body = recommend(api, business, event_for(api, business, "renewal_rate_pct")).json()
    assert body["options"][0]["title"] == "Talk to clients before their renewal is due"


def test_fewer_leads_suggests_asking_for_introductions(api, db, business):
    from tests.test_detection import Values
    from tests.test_detection import run as detect
    from tests.test_recommendations import event_for, recommend

    values = Values(db, business)
    values.put("new_leads", date(2026, 2, 1), 40, None)
    values.put("new_leads", date(2026, 3, 1), 15, 40)
    detect(db, business)
    body = recommend(api, business, event_for(api, business, "new_leads")).json()
    assert body["options"][0]["title"] == "Ask your best clients for introductions"
