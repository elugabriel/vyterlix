# ruff: noqa: E501, F811
"""The client's own example, start to finish (feedback 15/10/2026, section 3): customers came back
less often, the alert opens a recommendation on the same screen, accepting it makes a task with a
date and a person, a reminder arrives, finishing it asks what was done, and later the new figure
is compared and the result is told in plain words."""

from datetime import date, timedelta

import pytest

from app.core.uk import today_uk
from tests.test_actions import set_dates
from tests.test_alerts import alerts_of, evaluate
from tests.test_detection import Values, run
from tests.test_follow_up_nudges import inbox, sweep
from tests.test_health import ORGS
from tests.test_outcomes import check

CODE = "customer_retention_pct"


@pytest.fixture
def fell(db, business):
    """Of last month's customers, 38 in 100 came back in February and only 26 in March."""
    values = Values(db, business)
    values.put(CODE, date(2026, 2, 1), 38, None)
    values.put(CODE, date(2026, 3, 1), 26, 38)
    run(db, business)
    return business


def card_of(api, business):
    evaluate(api, business)
    for alert in alerts_of(api, business):
        res = api.get(
            f"{ORGS}/{business[0]}/alerts/{alert['id']}/card", headers=business[2]["owner"]
        )
        if res.status_code == 200 and (res.json().get("chart") or {}).get("code") == CODE:
            return alert, res.json()
    raise AssertionError("no alert about customers coming back")


def test_the_whole_loop_for_customers_who_stopped_coming_back(api, db, fell):
    # 1 + 2. Detect and explain: the alert opens as a card that says what happened and how bad
    alert, card = card_of(api, fell)
    assert card["tone"] == "bad" and "came back" in card["headline"].lower()
    assert card["priority"]["label"] in ("High", "Medium") and card["priority"]["reason"]
    assert card["numbers"] and "26" in card["numbers"] and "38" in card["numbers"]
    assert card["chart"]["marked"] == "2026-03-01"

    # 3. Recommend: contact the people who have not been back, with the reason and a date
    assert card["state"] == "open" and card["can_decide"] is True
    suggestion = card["suggestion"]
    assert suggestion["title"] == "Win back the customers who stopped coming"
    assert (
        any("message" in step.lower() for step in suggestion["steps"])
        and suggestion["why_this_one"]
    )
    assert suggestion["due_by"] >= today_uk().isoformat()

    # 4. Decide and assign: accepting makes a task with a person and dates
    res = api.post(f"{ORGS}/{fell[0]}/alerts/{alert['id']}/accept", headers=fell[2]["owner"])
    assert res.status_code == 200, res.text
    action = res.json()["action"]
    assert action["status"] == "accepted" and action["owner"] and action["target_date"]
    detail = api.get(f"{ORGS}/{fell[0]}/actions/{action['id']}", headers=fell[2]["owner"]).json()
    assert detail["decision"]["baseline_value"] == "26.0000" or detail["decision"][
        "baseline_value"
    ].startswith("26")

    # 5. Follow up: two days before it is due, the person doing it is reminded
    set_dates(
        db, fell, action["id"], today_uk() - timedelta(days=1), today_uk() + timedelta(days=2)
    )
    assert sweep(db, fell, today_uk())["due_soon"] == 1
    assert any(n["title"].startswith("Due in 2 days") for n in inbox(api, fell))

    # ...and when it is finished they are asked what was done and how many it reached
    done = api.post(
        f"{ORGS}/{fell[0]}/actions/{action['id']}/status",
        json={"status": "completed", "what_was_done": "Messaged 40 regulars", "reached": 40},
        headers=fell[2]["owner"],
    ).json()
    assert done["follow_up"]["status"] == "scheduled" and done["follow_up"]["measure_month"]

    # 6. Measure and learn: the new figure is compared, in plain words, and remembered
    Values(db, fell).put(CODE, date.fromisoformat(done["follow_up"]["measure_month"]), 34, None)
    result = check(db, fell, done)
    assert result.outcome in ("successful", "partially_successful") and result.reason
    again = api.get(f"{ORGS}/{fell[0]}/actions/{action['id']}", headers=fell[2]["owner"]).json()
    assert again["outcome"]["outcome"] == result.outcome
    assert again["updates"][-1]["kind"] == "outcome"
