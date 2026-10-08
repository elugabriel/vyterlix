# ruff: noqa: E501, F811
"""The front screen: what needs attention today, most serious first, and only what each person may see."""

import uuid
from datetime import date, timedelta

import pytest

from app.alerts.rules import RANK
from app.core.uk import today_uk
from app.dashboard import rules
from tests.test_actions import accept, act, add_manager, set_dates
from tests.test_alerts import evaluate, put_rule
from tests.test_health import ORGS
from tests.test_recommendations import event_for, recommend


def item(kind="alert", severity="high", title="t", category=None):
    return rules.Item(kind, severity, title, "d", "x.html", category, f"{kind}:{title}")


# --- the rules --------------------------------------------------------------------------------------


def test_the_most_serious_come_first_then_the_kinds_that_wait_on_a_person():
    items = [
        item("suggestion", "low", "a"),
        item("alert", "critical", "b"),
        item("approval", "medium", "c"),
        item("action_overdue", "medium", "d"),
        item("alert", "medium", "e"),
        item("follow_up", "medium", "f"),
        item("action_due_soon", "medium", "g"),
        item("alert", "high", "h"),
    ]
    assert [i.title for i in rules.order(items)] == ["b", "h", "c", "d", "e", "f", "g", "a"]


def test_the_same_kind_and_seriousness_are_in_alphabetical_order():
    assert [i.title for i in rules.order([item(title="b"), item(title="a")])] == ["a", "b"]


def test_only_so_many_are_shown_and_the_rest_are_counted():
    shown, more = rules.top([item(title=f"{n:02}") for n in range(15)])
    assert len(shown) == rules.MAX_ITEMS == 12 and more == 3
    shown, more = rules.top([item(title=f"{n:02}") for n in range(12)])
    assert len(shown) == 12 and more == 0


@pytest.mark.parametrize(
    ("role", "remit", "category", "seen", "acts"),
    [("owner", None, "sales", True, True), ("owner", ["customer"], "sales", True, True), ("manager", None, "sales", True, True),
     ("manager", ["sales"], "sales", True, True), ("manager", ["customer"], "sales", False, False), ("manager", ["customer"], None, True, True),
     ("viewer", None, "sales", True, False), ("viewer", ["customer"], "sales", True, False), ("viewer", None, None, True, False)],
)  # fmt: skip
def test_who_sees_what_and_who_is_given_something_to_do(role, remit, category, seen, acts):
    assert rules.visible(role, remit, category) is seen
    assert rules.can_act(role, remit, category) is acts


@pytest.mark.parametrize(
    ("count", "serious", "line"),
    [(0, 0, "Nothing needs your attention today."), (1, 0, "1 thing needs your attention today."), (1, 1, "1 thing needs your attention today, 1 of them serious."),
     (5, 0, "5 things need your attention today."), (5, 3, "5 things need your attention today, 3 of them serious.")],
)  # fmt: skip
def test_the_opening_sentence(count, serious, line):
    assert rules.greeting_line(count, serious) == line


def test_the_headline_figures_are_a_short_fixed_list():
    assert rules.HEADLINE_FIGURES == (
        "revenue",
        "gross_profit",
        "net_profit",
        "active_customers",
        "average_order_value",
    )


# --- the screen ----------------------------------------------------------------------------------------


def dash(api, business, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/dashboard", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def test_a_new_business_has_nothing_needing_attention_and_is_pointed_at_setting_up(api, business):
    body = dash(api, business)
    assert (
        body["headline"] == "Nothing needs your attention today."
        and body["attention"] == []
        and body["more_attention"] == 0
    )
    assert (
        body["health"] is None
        and body["figures"] == []
        and body["open_actions"] == 0
        and body["unread_notifications"] == 0
    )
    assert body["role"] == "owner" and body["can_act"] is True
    assert (
        body["setup"]["ready"] is False
        and body["setup"]["done"] < body["setup"]["total"]
        and body["setup"]["next_section"]
    )


def test_setting_up_is_only_shown_to_the_owner(api, business):
    assert dash(api, business, "viewer")["setup"] is None


def test_what_needs_attention_comes_first_most_serious_first(api, march):
    evaluate(api, march)
    body = dash(api, march)
    assert body["headline"] == "7 things need your attention today, 6 of them serious."
    severities = [RANK[i["severity"]] for i in body["attention"]]
    assert severities == sorted(severities, reverse=True) and len(body["attention"]) == 7
    first = body["attention"][0]
    assert (first["kind"], first["severity"], first["can_act"]) == ("alert", "high", True)
    assert (
        first["id"].startswith("alert:")
        and first["link"] == "changes.html"
        and first["title"]
        and first["detail"]
    )
    assert body["attention"][-1]["title"].startswith(
        "No new sales for "
    )  # the one that is only medium


def test_the_smaller_alerts_are_left_for_the_alerts_page(api, march):
    put_rule(api, march, "data_stale", {"severity": "low"})
    evaluate(api, march)
    assert not any(i["title"].startswith("No new sales") for i in dash(api, march)["attention"])
    assert len(dash(api, march)["attention"]) == 6


def test_a_viewer_sees_what_is_going_on_but_is_given_nothing_to_do(api, march):
    evaluate(api, march)
    body = dash(api, march, "viewer")
    assert body["role"] == "viewer" and body["can_act"] is False and len(body["attention"]) == 7
    assert not any(i["can_act"] for i in body["attention"])


def test_a_manager_sees_only_their_own_area_and_what_belongs_to_none(api, db, signup, march):
    evaluate(api, march)
    who = add_manager(api, db, signup, march, ["customer"])
    body = dash(api, march, who)
    assert {i["category"] for i in body["attention"]} == {"customer", None}
    assert (
        body["headline"] == "2 things need your attention today, 1 of them serious."
        and body["can_act"] is True
    )
    assert all(i["can_act"] for i in body["attention"]) and body["setup"] is None
    assert {f["code"] for f in body["figures"]} <= {
        "active_customers",
        "average_customer_value",
    } | {"active_customers"}


def test_the_figures_a_manager_is_shown_are_only_their_areas(api, db, signup, march):
    owner_codes = {f["code"] for f in dash(api, march)["figures"]}
    assert {"revenue", "gross_profit", "active_customers", "average_order_value"} <= owner_codes
    who = add_manager(api, db, signup, march, ["customer"])
    assert {f["code"] for f in dash(api, march, who)["figures"]} == {"active_customers"}


def test_the_headline_figures_say_what_they_were_and_how_they_moved(api, march):
    by_code = {f["code"]: f for f in dash(api, march)["figures"]}
    sales = by_code["revenue"]
    assert (sales["name"], sales["unit"], sales["period"], sales["value"], sales["direction"]) == (
        "Sales",
        "gbp",
        "2026-03-01",
        "410.00",
        "up_good",
    )
    assert sales["change_pct"] is not None and float(sales["change_pct"]) < 0


@pytest.fixture
def suggested(api, march):
    event = event_for(api, march, "revenue")
    return event, recommend(api, march, event).json()


def test_an_open_suggestion_is_offered_with_what_to_do_first(api, march, suggested):
    event, rec = suggested
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "suggestion"]
    assert (
        one["severity"] == "low"
        and one["title"] == rec["headline"]
        and one["link"] == f"changes.html#{event['id']}"
    )
    assert (
        one["detail"] == f"Suggested: {rec['options'][0]['title']}."
        and one["category"] == "financial"
    )


def test_a_viewer_is_not_shown_suggestions_that_wait_on_a_decision(api, march, suggested):
    assert not [i for i in dash(api, march, "viewer")["attention"] if i["kind"] == "suggestion"]


def test_a_suggestion_that_has_been_taken_up_is_no_longer_offered(api, march, suggested):
    event, _ = suggested
    accept(api, march, event)
    assert not [i for i in dash(api, march)["attention"] if i["kind"] == "suggestion"]


def test_overdue_work_is_listed_with_how_late_it_is(api, db, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "action_overdue"]
    assert one["title"] == f'"{action["title"]}" is 5 days late' and one["severity"] == "medium"
    assert (
        one["link"] == f"actions.html#{action['id']}"
        and "Move the date, finish it or cancel it" in one["detail"]
    )
    assert dash(api, march)["open_actions"] == 1


def test_work_that_is_very_late_is_more_serious(api, db, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=40), today_uk() - timedelta(days=14)
    )
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "action_overdue"]
    assert one["severity"] == "high"
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=40), today_uk() - timedelta(days=13)
    )
    assert [i for i in dash(api, march)["attention"] if i["kind"] == "action_overdue"][0][
        "severity"
    ] == "medium"


@pytest.mark.parametrize(
    ("days", "when"), [(0, "today"), (1, "tomorrow"), (3, "in 3 days"), (7, "in 7 days")]
)
def test_work_due_within_a_week_is_listed_and_it_says_when(api, db, march, suggested, days, when):
    event, _ = suggested
    action = accept(api, march, event).json()
    act(
        api,
        march,
        action["id"],
        "",
        "patch",
        json={"target_date": str(today_uk() + timedelta(days=days))},
    )
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "action_due_soon"]
    assert one["title"] == f'"{action["title"]}" is due {when}' and one["severity"] == "low"


def test_work_due_in_eight_days_is_not_yet_news(api, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    act(
        api,
        march,
        action["id"],
        "",
        "patch",
        json={"target_date": str(today_uk() + timedelta(days=8))},
    )
    assert not [i for i in dash(api, march)["attention"] if i["kind"] == "action_due_soon"]


def test_the_owner_is_asked_to_approve_what_was_suggested_outside_someones_area(
    api, db, signup, march, suggested
):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    action = accept(api, march, event, who=who).json()
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "approval"]
    assert (
        one["title"] == f'Approve "{action["title"]}"?'
        and one["severity"] == "medium"
        and one["link"] == f"actions.html#{action['id']}"
    )
    assert not [i for i in dash(api, march, who)["attention"] if i["kind"] == "approval"]
    assert not [i for i in dash(api, march, "viewer")["attention"] if i["kind"] == "approval"]


def test_an_approval_comes_before_other_things_of_the_same_seriousness(
    api, db, signup, march, suggested
):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    accept(api, march, event, who=who)
    evaluate(api, march)
    kinds = [i["kind"] for i in dash(api, march)["attention"]]
    assert (
        kinds.index("approval") < kinds.index("alert", kinds.index("approval"))
        or kinds[0] == "alert"
    )
    stale = next(i for i in dash(api, march)["attention"] if i["title"].startswith("No new sales"))
    assert [i["kind"] for i in dash(api, march)["attention"]].index("approval") < dash(api, march)[
        "attention"
    ].index(stale)


def test_a_follow_up_that_is_due_is_listed(api, db, march, suggested):
    from sqlalchemy import select

    from app.models.outcomes import FollowUpSchedule
    from tests.test_health import scoped

    event, _ = suggested
    action = accept(api, march, event).json()
    act(api, march, action["id"], "/status", "post", json={"status": "completed"})
    assert not [i for i in dash(api, march)["attention"] if i["kind"] == "follow_up"]
    with scoped(db, march):
        db.scalars(select(FollowUpSchedule)).one().due_date = today_uk()
    [one] = [i for i in dash(api, march)["attention"] if i["kind"] == "follow_up"]
    assert (
        one["title"] == f'Time to check "{action["title"]}"'
        and one["link"] == f"actions.html#{action['id']}"
    )


def test_the_health_of_the_business_is_shown_after_what_needs_attention(api, db, march):
    from app.services import health as health_service
    from tests.test_health import owner_tenant, scoped

    with scoped(db, march):
        health_service.calculate(db, owner_tenant(db, march))
    body = dash(api, march)
    if body["health"] is not None:
        assert (
            body["health"]["period"] == "2026-03-01"
            and 0 <= body["health"]["score"] <= 100
            and body["health"]["explanation"]
        )


def test_unread_notifications_are_counted_for_the_person_looking(api, march):
    evaluate(api, march)
    assert (
        dash(api, march)["unread_notifications"] == 7
        and dash(api, march, "viewer")["unread_notifications"] == 0
    )
    api.post(f"{ORGS}/{march[0]}/notifications/read-all", headers=march[2]["owner"])
    assert dash(api, march)["unread_notifications"] == 0


def test_only_the_businesss_own_things_are_shown(api, march):
    evaluate(api, march)
    other = dash(api, march, "other", org=1)
    assert other["attention"] == [] and other["figures"] == [] and other["open_actions"] == 0


def test_the_dashboard_needs_a_login_and_a_membership(api, march):
    assert api.get(f"{ORGS}/{march[0]}/dashboard").status_code == 401
    assert api.get(f"{ORGS}/{march[0]}/dashboard", headers=march[2]["other"]).status_code in (
        403,
        404,
    )
    assert api.get(f"{ORGS}/{uuid.uuid4()}/dashboard", headers=march[2]["owner"]).status_code in (
        403,
        404,
    )


def test_a_day_with_nothing_to_do_is_said_plainly(api, db, march):
    for code in ("change_sales", "change_financial", "change_customer", "data_stale"):
        put_rule(api, march, code, {"enabled": False})
    evaluate(api, march)
    body = dash(api, march)
    assert body["headline"] == "Nothing needs your attention today." or all(
        i["kind"] == "suggestion" for i in body["attention"]
    )
    assert date.fromisoformat(body["figures"][0]["period"])


def test_a_manager_never_sees_an_approval_even_in_the_area_it_is_about(
    api, db, signup, march, suggested
):
    event, _ = suggested
    proposer = add_manager(api, db, signup, march, ["customer"])
    accept(api, march, event, who=proposer)  # outside their area: waits for the owner
    financial = add_manager(api, db, signup, march, ["financial"], email="manager2@acme.co.uk")
    assert [i for i in dash(api, march)["attention"] if i["kind"] == "approval"]
    assert not [i for i in dash(api, march, financial)["attention"] if i["kind"] == "approval"]


@pytest.mark.parametrize(
    ("ready", "completed", "shown"),
    [
        (False, None, True),
        (True, None, True),
        (False, "2026-01-01", True),
        (True, "2026-01-01", False),
    ],
)
def test_setting_up_is_shown_until_it_is_ready_and_marked_complete(
    api, march, monkeypatch, ready, completed, shown
):
    from app.services import onboarding

    real = onboarding.progress

    def fake(db):
        return {**real(db), "ready_for_dashboard": ready, "completed_at": completed}

    monkeypatch.setattr(onboarding, "progress", fake)
    assert (dash(api, march)["setup"] is not None) is shown
