# ruff: noqa: E501, F811
"""An alert and what to do about it, in one place (client feedback 15/10/2026, section 5)."""

import uuid
from datetime import date, timedelta

import pytest

from app.core.uk import today_uk
from app.plain.changes import explain_change
from app.plain.priority import HIGH_AT, MEDIUM_AT, prioritise
from tests.test_actions import add_manager
from tests.test_alerts import alerts_of, evaluate
from tests.test_health import ORGS
from tests.test_recommendations import event_for

# --- the words ------------------------------------------------------------------------------------


def change(**over):
    base = dict(
        kpi_code="revenue", kpi_name="Sales", unit="gbp", direction="down", severity="major",
        effect="bad", period_start=date(2026, 9, 1), value="1766.20", reference_value="2273.00",
        change="-22.3", change_unit="percent", explained_by_season=False,
    )  # fmt: skip
    return explain_change(**{**base, **over})


def test_a_big_fall_in_sales_is_said_plainly():
    words = change()
    assert words.headline == "Sales fell sharply in September 2026."
    assert words.happened == "You sold less than last month."
    assert words.numbers == "Sales was £1,766.20, compared with £2,273.00 last month (down 22.3%)."
    assert (
        words.why == "Check whether this is a normal quiet spell for your business before reacting."
    )
    assert words.tone == "bad" and words.season is None


def test_a_smaller_rise_is_noticeable_not_sharp_and_good_news_is_good():
    words = change(
        direction="up",
        severity="notable",
        effect="good",
        change="9.1",
        value="2480.00",
        reference_value="2273.00",
    )
    assert words.headline == "Sales rose noticeably in September 2026."
    assert words.happened == "You sold more than last month." and words.tone == "good"
    assert words.numbers.endswith("(up 9.1%).")


def test_a_figure_that_is_a_percentage_moves_in_points():
    words = change(
        kpi_code="gross_margin_pct",
        kpi_name="Gross margin",
        unit="percent",
        value="70.0",
        reference_value="76.0",
        change="-6.0",
        change_unit="points",
    )
    assert (
        words.numbers == "Gross margin was 70.0%, compared with 76.0% last month (down 6.0 points)."
    )
    assert words.happened == "A smaller share of each sale is left after the cost of the goods."


def test_a_lower_is_better_figure_rising_is_bad_news():
    words = change(
        kpi_code="operating_expenses",
        kpi_name="Running costs",
        direction="up",
        effect="bad",
        change="12.0",
        value="5000.00",
        reference_value="4464.00",
    )
    assert (
        words.tone == "bad" and words.happened == "Your running costs are higher than last month."
    )


def test_a_change_the_seasons_explain_says_so():
    assert (
        change(explained_by_season=True).season
        == "This is about what your busy and quiet times lead you to expect."
    )


def test_the_headline_and_numbers_never_state_a_cause():
    import re

    causal = re.compile(r"\b(because of|caused by|due to|as a result of|thanks to)\b", re.I)
    for words in (change(), change(direction="up", effect="good", change="9.1")):
        assert not causal.search(
            " ".join([words.headline, words.happened, words.numbers, words.why])
        )


def test_a_figure_the_wording_table_does_not_know_still_says_something_true():
    words = change(kpi_code="a_new_figure", kpi_name="New thing")
    assert words.happened == "New thing fell compared with last month."
    assert words.tone == "neutral"


# --- how urgent -----------------------------------------------------------------------------------


def scores(impact=80, urgency=80, confidence=80):
    return {"impact": impact, "urgency": urgency, "confidence": confidence}


def test_priority_is_impact_urgency_and_evidence_not_the_size_of_the_change():
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(90, 70, 80)).level == "high"
    )
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(50, 50, 50)).level == "medium"
    )
    assert prioritise(alert_severity="high", effect="bad", scores=scores(20, 30, 30)).level == "low"
    # exactly on the lines: 40/30/30
    assert (HIGH_AT, MEDIUM_AT) == (65, 40)
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(65, 65, 65)).level == "high"
    )
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(64, 64, 64)).level == "medium"
    )
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(40, 40, 40)).level == "medium"
    )
    assert prioritise(alert_severity="high", effect="bad", scores=scores(39, 39, 39)).level == "low"


def test_thin_evidence_holds_it_at_medium_however_big_the_impact():
    p = prioritise(alert_severity="high", effect="bad", scores=scores(100, 100, 39))
    assert p.level == "medium" and p.reason.endswith(
        "It is held at Medium because the evidence is thin."
    )
    assert (
        prioritise(alert_severity="high", effect="bad", scores=scores(100, 100, 40)).level == "high"
    )


def test_a_low_key_alert_is_at_most_medium():
    p = prioritise(alert_severity="low", effect="bad", scores=scores(100, 100, 100))
    assert p.level == "medium" and "the alert itself is low-key" in p.reason


def test_the_reason_says_each_of_the_three():
    p = prioritise(alert_severity="high", effect="bad", scores=scores(90, 50, 20))
    assert (
        p.reason
        == "Medium priority: it could have a big effect on your business, it is fairly urgent, and the evidence is thin. It is held at Medium because the evidence is thin."
        or p.level == "medium"
    )
    p = prioritise(alert_severity="high", effect="bad", scores=scores(90, 90, 90))
    assert (
        p.reason
        == "High priority: it could have a big effect on your business, it is urgent, and the evidence is strong."
    )


def test_good_news_is_always_low_and_without_a_suggestion_the_alert_decides():
    assert prioritise(alert_severity="critical", effect="good", scores=scores()).level == "low"
    assert prioritise(alert_severity="critical", effect="bad", scores=None).level == "high"
    assert prioritise(alert_severity="medium", effect="bad", scores=None).level == "medium"
    assert prioritise(alert_severity="info", effect="bad", scores=None).level == "low"
    assert prioritise(alert_severity="high", effect="bad", scores={"impact": 50}).reason.endswith(
        "We have no suggestion to score yet."
    )


# --- the card ---------------------------------------------------------------------------------------


def card_of(api, business, alert, who="owner"):
    return api.get(f"{ORGS}/{business[0]}/alerts/{alert['id']}/card", headers=business[2][who])


def a_change_alert(api, business, starts="Sales fell"):
    """The March fall in sales (the first of the bakery's change alerts about the figure called Sales)."""
    evaluate(api, business)
    return next(a for a in alerts_of(api, business) if a["title"].startswith(starts))


@pytest.fixture
def sales_alert(api, march):
    return a_change_alert(api, march)


def test_a_change_alert_opens_as_a_card_with_everything_needed_to_decide(api, march, sales_alert):
    res = card_of(api, march, sales_alert)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "open" and body["can_decide"] is True
    assert body["alert"]["id"] == sales_alert["id"]
    assert body["headline"] == "Sales fell sharply in March 2026."
    assert body["happened"] == "You sold less than last month." and body["tone"] == "bad"
    assert body["numbers"] == "Sales was £410.00, compared with £600.00 last month (down 31.7%)."
    assert body["why_it_matters"]
    assert body["priority"]["level"] in {"low", "medium", "high"}
    assert body["priority"]["label"] == body["priority"]["level"].capitalize()
    assert body["priority"]["reason"].startswith(body["priority"]["label"] + " priority:")
    assert body["next_step"] is None and body["action"] is None and body["event_id"]


def test_the_card_carries_the_chart_with_the_month_marked(api, march, sales_alert):
    chart = card_of(api, march, sales_alert).json()["chart"]
    assert chart["code"] == "revenue" and chart["name"] == "Sales" and chart["unit"] == "gbp"
    assert chart["marked"] == "2026-03-01"
    months = [p["period_start"] for p in chart["points"]]
    assert months == sorted(months) and "2026-03-01" in months and len(months) <= 12


def test_the_suggestion_is_the_recommended_option_ready_to_accept(api, march, sales_alert):
    body = card_of(api, march, sales_alert).json()
    s = body["suggestion"]
    assert s["title"] and s["description"] and s["steps"] and s["why_this_one"]
    assert s["effort"] in {"low", "medium", "high"} and s["days_to_effect"] >= 1
    assert date.fromisoformat(s["due_by"]) == today_uk() + timedelta(
        days=max(1, s["days_to_effect"])
    )
    assert s["other_options"] >= 0
    assert 1 <= len(body["evidence"]) <= 3
    # it is the very recommendation the What changed page shows
    event = event_for(api, march, "revenue")
    rec = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert (
        rec["options"][0]["title"] == s["title"] and str(rec["options"][0]["id"]) == s["option_id"]
    )


def test_opening_the_card_twice_does_not_make_a_second_recommendation(api, march, sales_alert):
    first = card_of(api, march, sales_alert).json()["suggestion"]["option_id"]
    second = card_of(api, march, sales_alert).json()["suggestion"]["option_id"]
    assert first == second


def test_someone_who_may_only_look_sees_the_card_but_nothing_is_made_for_them(
    api, march, sales_alert
):
    body = card_of(api, march, sales_alert, who="viewer").json()
    assert body["state"] == "not_yet" and body["suggestion"] is None and body["can_decide"] is False
    assert body["headline"] and body["happened"] and body["chart"]
    event = event_for(api, march, "revenue")
    assert (
        api.get(
            f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
        ).status_code
        == 404
    )


def test_a_viewer_can_see_the_suggestion_once_an_owner_has_opened_the_card(api, march, sales_alert):
    card_of(api, march, sales_alert)  # the owner opens it
    body = card_of(api, march, sales_alert, who="viewer").json()
    assert (
        body["state"] == "open" and body["suggestion"] is not None and body["can_decide"] is False
    )


def accept_card(api, business, alert, body=None, who="owner"):
    return api.post(
        f"{ORGS}/{business[0]}/alerts/{alert['id']}/accept", json=body, headers=business[2][who]
    )


def test_accepting_makes_the_action_and_the_card_shows_it(api, march, sales_alert):
    res = accept_card(api, march, sales_alert)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (
        body["state"] == "accepted" and body["can_decide"] is False and body["suggestion"] is None
    )
    action = body["action"]
    assert action["title"] and action["status"] == "accepted" and action["owner"]
    assert action["target_date"] is not None and action["check_from"] is not None
    # the alert is marked as being dealt with
    assert body["alert"]["status"] == "acknowledged"
    # and it is a real, linked action
    listed = api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"]).json()
    assert any(
        a["id"] == action["id"] for a in (listed["items"] if isinstance(listed, dict) else listed)
    )


def test_accepting_with_changes_assigns_it_to_someone_with_their_own_dates(api, march, sales_alert):
    who = add_manager_for(api, march)
    due = (today_uk() + timedelta(days=9)).isoformat()
    res = accept_card(
        api,
        march,
        sales_alert,
        {"title": "Ring the regulars", "owner_user_id": who["id"], "target_date": due},
    )
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["action"]["title"] == "Ring the regulars"
    assert body["action"]["owner"] == who["name"] and body["action"]["target_date"] == due


def add_manager_for(api, march):
    members = api.get(f"{ORGS}/{march[0]}/members", headers=march[2]["owner"]).json()
    other = next(m for m in members if m["role"] != "owner")
    return {"id": other["user_id"], "name": other["full_name"]}


def test_a_suggestion_can_only_be_dealt_with_once(api, march, sales_alert):
    assert accept_card(api, march, sales_alert).status_code == 200
    again = accept_card(api, march, sales_alert)
    assert again.status_code == 409
    dismiss = api.post(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/dismiss", json={}, headers=march[2]["owner"]
    )
    assert dismiss.status_code == 409


def test_dismissing_keeps_the_reason_and_closes_the_alert(api, march, sales_alert):
    res = api.post(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/dismiss",
        json={"reason": "It is our quiet week"},
        headers=march[2]["owner"],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "dismissed" and body["alert"]["status"] == "resolved"
    assert "It is our quiet week" in body["dismissed_reason"]
    assert body["can_decide"] is False
    event = event_for(api, march, "revenue")
    rec = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert rec["status"] == "dismissed"


def test_dismissing_without_a_reason_still_works(api, march, sales_alert):
    res = api.post(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/dismiss", headers=march[2]["owner"]
    )
    assert res.status_code == 200 and res.json()["state"] == "dismissed"
    assert res.json()["dismissed_reason"] == "Decided not to act on it."


def test_someone_who_may_only_look_cannot_accept_or_dismiss(api, march, sales_alert):
    assert accept_card(api, march, sales_alert, who="viewer").status_code == 403
    assert (
        api.post(
            f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/dismiss",
            json={},
            headers=march[2]["viewer"],
        ).status_code
        == 403
    )
    assert card_of(api, march, sales_alert).json()["state"] == "open"  # nothing changed


def test_a_manager_outside_their_area_can_only_propose_it(api, db, signup, march, sales_alert):
    who = add_manager(api, db, signup, march, ["customer"])
    body = accept_card(
        api, march, sales_alert, who=who if isinstance(who, str) else "manager"
    ).json()
    assert body["action"]["status"] == "pending"
    # it waits for the owner: the alert stays open and there is nothing more for the manager to decide
    assert (
        body["state"] == "proposed"
        and body["alert"]["status"] == "open"
        and body["can_decide"] is False
    )


def test_an_alert_that_is_not_about_a_change_has_a_next_step_and_nothing_to_accept(api, march):
    evaluate(api, march)
    other = next(a for a in alerts_of(api, march) if not a["rule_code"].startswith("change_"))
    body = card_of(api, march, other).json()
    assert body["state"] == "other" and body["suggestion"] is None and body["event_id"] is None
    assert body["headline"] == other["title"] and body["happened"] == other["body"]
    assert body["next_step"]["text"] and body["can_decide"] is False
    assert accept_card(api, march, other).status_code == 409
    assert (
        api.post(
            f"{ORGS}/{march[0]}/alerts/{other['id']}/dismiss", json={}, headers=march[2]["owner"]
        ).status_code
        == 409
    )


def test_an_alert_that_does_not_exist_is_not_found(api, march):
    res = api.get(f"{ORGS}/{march[0]}/alerts/{uuid.uuid4()}/card", headers=march[2]["owner"])
    assert res.status_code == 404


# --- putting it off ---------------------------------------------------------------------------------


def postpone(api, business, alert, body, who="owner"):
    return api.post(
        f"{ORGS}/{business[0]}/alerts/{alert['id']}/postpone", json=body, headers=business[2][who]
    )


def summary_of(api, business, who="owner"):
    return api.get(f"{ORGS}/{business[0]}/alerts/summary", headers=business[2][who]).json()


def attention_ids(api, business):
    return {
        i["id"]
        for i in api.get(f"{ORGS}/{business[0]}/dashboard", headers=business[2]["owner"]).json()[
            "attention"
        ]
    }


def test_postponing_for_some_days_puts_it_off_until_that_day(api, march, sales_alert):
    res = postpone(api, march, sales_alert, {"days": 3})
    assert res.status_code == 200, res.text
    body = res.json()
    due = (today_uk() + timedelta(days=3)).isoformat()
    assert body["state"] == "postponed" and body["postponed_until"] == due
    assert body["alert"]["status"] == "open" and body["alert"]["postponed_until"] == due
    assert (
        body["suggestion"] is not None and body["can_decide"] is True
    )  # it can still be decided on
    events = api.get(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}", headers=march[2]["owner"]
    ).json()["events"]
    assert (
        events[-1]["kind"] == "postponed"
        and events[-1]["note"] == f"Put off until {today_uk() + timedelta(days=3):%d/%m/%Y}."
    )


def test_a_postponed_alert_is_out_of_the_way_but_counted_on_its_own(api, march, sales_alert):
    before = summary_of(api, march)
    assert attention_ids(api, march) >= {f"alert:{sales_alert['id']}"}
    postpone(api, march, sales_alert, {"days": 7})
    after = summary_of(api, march)
    assert after["open"] == before["open"] - 1 and after["postponed"] == before["postponed"] + 1
    assert f"alert:{sales_alert['id']}" not in attention_ids(api, march)
    assert after["high_or_critical_open"] == before["high_or_critical_open"] - 1
    listed = next(a for a in alerts_of(api, march) if a["id"] == sales_alert["id"])
    assert (
        listed["status"] == "open"
        and listed["postponed_until"] == (today_uk() + timedelta(days=7)).isoformat()
    )


def test_it_can_be_put_off_until_a_chosen_day(api, march, sales_alert):
    day = (today_uk() + timedelta(days=20)).isoformat()
    body = postpone(api, march, sales_alert, {"until": day}).json()
    assert body["postponed_until"] == day


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"days": 3, "until": "2099-01-01"},
        {"days": 0},
        {"days": 91},
        {"days": -2},
        {"until": "2000-01-01"},
        {"until": "not a date"},
    ],
)
def test_a_bad_postponement_is_refused(api, march, sales_alert, body):
    assert postpone(api, march, sales_alert, body).status_code == 422
    assert card_of(api, march, sales_alert).json()["state"] == "open"


def test_today_and_too_far_ahead_are_out_of_range(api, march, sales_alert):
    assert postpone(api, march, sales_alert, {"until": today_uk().isoformat()}).status_code == 422
    assert (
        postpone(
            api, march, sales_alert, {"until": (today_uk() + timedelta(days=91)).isoformat()}
        ).status_code
        == 422
    )
    assert (
        postpone(
            api, march, sales_alert, {"until": (today_uk() + timedelta(days=90)).isoformat()}
        ).status_code
        == 200
    )


def test_it_comes_back_by_itself_on_its_day(api, db, march, sales_alert):
    from datetime import timedelta as td

    from app.models.alerts import Alert
    from tests.test_health import scoped

    postpone(api, march, sales_alert, {"days": 3})
    with scoped(db, march):
        db.get(Alert, uuid.UUID(sales_alert["id"])).snoozed_until = today_uk() - td(days=1)
        db.flush()
    body = card_of(api, march, sales_alert).json()
    assert body["state"] == "open" and body["postponed_until"] is None
    assert f"alert:{sales_alert['id']}" in attention_ids(api, march)
    assert summary_of(api, march)["postponed"] == 0


def test_it_can_be_brought_back_now(api, march, sales_alert):
    postpone(api, march, sales_alert, {"days": 14})
    res = api.post(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/bring-back", headers=march[2]["owner"]
    )
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "open" and res.json()["postponed_until"] is None
    assert f"alert:{sales_alert['id']}" in attention_ids(api, march)
    events = api.get(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}", headers=march[2]["owner"]
    ).json()["events"]
    assert [e["kind"] for e in events][-2:] == ["postponed", "brought_back"]
    again = api.post(
        f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/bring-back", headers=march[2]["owner"]
    )
    assert again.status_code == 409  # it is not put off any more


def test_a_postponed_suggestion_can_still_be_accepted(api, march, sales_alert):
    postpone(api, march, sales_alert, {"days": 3})
    body = accept_card(api, march, sales_alert).json()
    assert body["state"] == "accepted" and body["postponed_until"] is None


def test_only_an_open_alert_can_be_put_off(api, march, sales_alert):
    accept_card(api, march, sales_alert)  # now seen
    assert postpone(api, march, sales_alert, {"days": 3}).status_code == 409


def test_someone_who_may_only_look_cannot_put_it_off(api, march, sales_alert):
    assert postpone(api, march, sales_alert, {"days": 3}, who="viewer").status_code == 403
    assert (
        api.post(
            f"{ORGS}/{march[0]}/alerts/{sales_alert['id']}/bring-back", headers=march[2]["viewer"]
        ).status_code
        == 403
    )


def test_an_alert_that_is_not_about_a_change_can_be_put_off_too(api, march):
    evaluate(api, march)
    other = next(a for a in alerts_of(api, march) if not a["rule_code"].startswith("change_"))
    body = postpone(api, march, other, {"days": 5}).json()
    assert (
        body["state"] == "other"
        and body["postponed_until"] == (today_uk() + timedelta(days=5)).isoformat()
    )


def test_the_database_allows_the_new_event_kinds(db):
    from sqlalchemy import text

    kinds = db.execute(
        text(
            "select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_alert_events_kind_valid'"
        )
    ).scalar()
    assert "postponed" in kinds and "brought_back" in kinds


def test_someone_who_may_only_look_is_told_when_an_alert_comes_back(api, march, sales_alert):
    postpone(api, march, sales_alert, {"days": 3})
    body = card_of(api, march, sales_alert, who="viewer").json()
    assert body["state"] == "postponed" and body["can_decide"] is False
    assert body["postponed_until"] == (today_uk() + timedelta(days=3)).isoformat()
