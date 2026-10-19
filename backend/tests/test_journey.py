# ruff: noqa: E501, F811
"""One change on one page, from what happened to how it turned out (client feedback sections 1 and 3)."""

from datetime import date

import pytest

from tests.test_alert_card import accept_card
from tests.test_alerts import alerts_of, evaluate
from tests.test_detection import Values, run
from tests.test_health import ORGS
from tests.test_outcomes import check
from tests.test_recommendations import event_for

CODE = "customer_retention_pct"


@pytest.fixture
def fell(db, business):
    values = Values(db, business)
    values.put(CODE, date(2026, 2, 1), 38, None)
    values.put(CODE, date(2026, 3, 1), 26, 38)
    run(db, business)
    return business


def journey(api, business, event, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/changes/{event['id']}/journey", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def states(body):
    return {s["key"]: s["state"] for s in body["steps"]}


def open_card(api, business):
    evaluate(api, business)
    for alert in alerts_of(api, business):
        res = api.get(
            f"{ORGS}/{business[0]}/alerts/{alert['id']}/card", headers=business[2]["owner"]
        )
        if res.status_code == 200 and (res.json().get("chart") or {}).get("code") == CODE:
            return alert
    raise AssertionError("no alert")


def test_a_change_nobody_has_looked_at_is_at_the_why_step(api, fell):
    body = journey(api, fell, event_for(api, fell, CODE))
    assert body["stage"] == "why" and [s["key"] for s in body["steps"]] == [
        "happened",
        "why",
        "suggest",
        "decide",
        "doing",
        "result",
    ]
    assert states(body) == {
        "happened": "done",
        "why": "current",
        "suggest": "todo",
        "decide": "todo",
        "doing": "todo",
        "result": "todo",
    }
    assert body["event"]["plain"]["headline"] and body["chart"]["marked"] == "2026-03-01"
    assert body["diagnosis"] is None and body["suggestion"] is None and body["action"] is None


def test_once_a_suggestion_is_made_it_waits_for_a_decision(api, fell):
    open_card(api, fell)
    body = journey(api, fell, event_for(api, fell, CODE))
    assert (
        body["stage"] == "decide"
        and states(body)["decide"] == "current"
        and states(body)["suggest"] == "done"
    )
    assert (
        body["suggestion"]["title"] == "Win back the customers who stopped coming"
        and body["suggestion"]["steps"]
    )
    assert body["alert_id"] is not None and body["can_decide"] is True
    assert next(s for s in body["steps"] if s["key"] == "decide")["note"] == "Waiting for you."


def test_accepting_moves_it_to_getting_it_done(api, fell):
    alert = open_card(api, fell)
    accept_card(api, fell, alert)
    body = journey(api, fell, event_for(api, fell, CODE))
    assert body["stage"] == "doing" and states(body) == {
        "happened": "done",
        "why": "done",
        "suggest": "done",
        "decide": "done",
        "doing": "current",
        "result": "todo",
    }
    assert (
        body["action"]["status"] == "accepted"
        and body["action"]["owner"]
        and body["alert_id"] == alert["id"]
    )


def test_the_result_is_told_at_the_end(api, db, fell):
    from tests.test_follow_up_nudges import status

    alert = open_card(api, fell)
    action = accept_card(api, fell, alert).json()["action"]
    done = status(api, fell, action["id"], {"status": "completed"}).json()
    waiting = journey(api, fell, event_for(api, fell, CODE))
    assert (
        waiting["stage"] == "result"
        and states(waiting)["result"] == "current"
        and waiting["follow_up"]["status"] == "scheduled"
    )
    assert "We will check from" in next(s for s in waiting["steps"] if s["key"] == "result")["note"]
    Values(db, fell).put(CODE, date.fromisoformat(done["follow_up"]["measure_month"]), 34, None)
    check(db, fell, done)
    finished = journey(api, fell, event_for(api, fell, CODE))
    assert states(finished)["result"] == "done" and finished["outcome"]["outcome"] in (
        "successful",
        "partially_successful",
    )


def test_good_news_has_nothing_to_decide(api, db, business):
    values = Values(db, business)
    values.put(CODE, date(2026, 2, 1), 26, None)
    values.put(CODE, date(2026, 3, 1), 38, 26)
    run(db, business)
    event = event_for(api, business, CODE)
    api.post(
        f"{ORGS}/{business[0]}/changes/{event['id']}/recommendation", headers=business[2]["owner"]
    )
    body = journey(api, business, event)
    assert body["suggestion"]["status"] == "no_action_needed"
    assert states(body)["decide"] == states(body)["doing"] == states(body)["result"] == "not_needed"


def test_a_viewer_can_read_it_but_not_decide(api, fell):
    open_card(api, fell)
    body = journey(api, fell, event_for(api, fell, CODE), who="viewer")
    assert body["can_decide"] is False and body["suggestion"] is not None


def test_another_business_cannot_see_it(api, fell):
    event = event_for(api, fell, CODE)
    res = api.get(f"{ORGS}/{fell[1]}/changes/{event['id']}/journey", headers=fell[2]["other"])
    assert res.status_code == 404
