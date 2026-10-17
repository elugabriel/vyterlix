# ruff: noqa: E501
"""Recommendations for the appointment figures (client feedback 15/10/2026, §4 examples E and F):
they cannot be broken down into causes, so the figure's own move is what is acted on."""

from datetime import date

import pytest

from app.recommend import rules
from tests.test_detection import Values, run
from tests.test_recommendations import event_for, finding, recommend

REMINDERS = "Send a reminder the day before every appointment"
POLICY = "Set and share a clear cancellation policy"
REBOOK = "Offer the next appointment before they leave"


def move(api, db, business, code, before, after):
    values = Values(db, business)
    values.put(code, date(2026, 2, 1), before, None)
    values.put(code, date(2026, 3, 1), after, before)
    run(db, business)
    return recommend(api, business, event_for(api, business, code)).json()


def titles(body):
    return [o["title"] for o in body["options"]]


def test_more_cancellations_are_answered_with_reminders_and_a_policy(api, db, business):
    body = move(api, db, business, "cancellation_rate_pct", 10, 25)
    assert body["status"] == "open"
    assert {REMINDERS, POLICY} <= set(titles(body))
    assert REBOOK not in titles(body)  # that is for a fall in rebooking


@pytest.mark.parametrize(
    ("code", "before", "after"), [("no_show_rate_pct", 5, 20), ("late_cancellations", 20, 60)]
)
def test_more_no_shows_and_late_cancellations_get_the_same_answers(
    api, db, business, code, before, after
):
    assert {REMINDERS, POLICY} <= set(titles(move(api, db, business, code, before, after)))


def test_fewer_people_booking_again_is_answered_by_offering_the_next_appointment(api, db, business):
    body = move(api, db, business, "rebooking_rate_pct", 60, 35)
    assert body["status"] == "open" and body["options"][0]["title"] == REBOOK
    assert REMINDERS not in titles(body)


def test_fewer_appointments_kept_is_answered_by_offering_the_next_appointment(api, db, business):
    assert REBOOK in titles(move(api, db, business, "appointments_kept", 40, 22))


def test_an_improvement_needs_no_action(api, db, business):
    body = move(api, db, business, "cancellation_rate_pct", 25, 10)
    assert body["status"] == "no_action_needed" and body["options"] == []


def test_the_recommendation_does_not_claim_to_know_why(api, db, business):
    body = move(api, db, business, "cancellation_rate_pct", 10, 25)
    assert any(
        e["evidence_type"] == "insufficient" for e in body["evidence"]
    )  # what we cannot tell
    first = body["options"][0]
    confidence = next(x["score"] for x in first["scores"] if x["key"] == "confidence")
    assert confidence < 60  # only the figure's own move is known
    assert first["impact_value"] != "0.00"  # so the result can be judged later


def test_the_rule_for_a_figure_that_moved_matches_only_that_kind_of_finding():
    from tests.test_recommendations import action

    reminder = action(kinds=(("figure_moved", None),), kpis=("no_show_rate_pct",))
    moved = finding(
        "figure_moved", "figure", "The figure itself", share=str(rules.FIGURE_ONLY_SHARE)
    )
    assert rules.matches(reminder, moved, "no_show_rate_pct")
    assert not rules.matches(reminder, moved, "revenue")  # a different figure
    assert not rules.matches(reminder, finding("price"), "no_show_rate_pct")
