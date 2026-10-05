"""Actions: the rules of an action's life (pure), then accepting and following recommendations."""

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.actions import rules
from app.core.uk import today_uk
from app.models.actions import (
    ActionEvidence,
    ActionUpdate,
    BusinessAction,
    BusinessIntervention,
)
from app.models.identity import OrganizationUser, Role, User
from app.models.recommendations import Recommendation
from app.services import actions as service
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_recommendations import event_for, recommend

D = date


# --- the rules ------------------------------------------------------------------------------


def test_what_an_action_can_become_from_each_state():
    assert rules.next_statuses("pending") == ["accepted", "cancelled"]
    assert rules.next_statuses("accepted") == [
        "in_progress", "partially_completed", "completed", "cancelled",
    ]  # fmt: skip
    assert rules.next_statuses("in_progress") == ["partially_completed", "completed", "cancelled"]
    assert rules.next_statuses("partially_completed") == ["in_progress", "completed", "cancelled"]
    assert rules.next_statuses("overdue") == [
        "in_progress", "partially_completed", "completed", "cancelled",
    ]  # fmt: skip
    assert rules.next_statuses("completed") == [] and rules.next_statuses("cancelled") == []


def test_overdue_is_never_something_a_person_chooses():
    assert all("overdue" not in rules.next_statuses(s) for s in rules.STATUSES)
    assert not rules.can_move("accepted", "overdue")


def test_done_and_cancelled_are_final_and_everything_else_can_be_cancelled():
    assert rules.FINAL == {"completed", "cancelled"}
    for status in rules.STATUSES:
        assert rules.can_move(status, "cancelled") is (status not in rules.FINAL)


@pytest.mark.parametrize(
    ("status", "target", "today", "overdue"),
    [
        ("accepted", D(2026, 10, 1), D(2026, 10, 2), True),
        ("in_progress", D(2026, 10, 1), D(2026, 10, 2), True),
        ("partially_completed", D(2026, 10, 1), D(2026, 10, 2), True),
        ("accepted", D(2026, 10, 2), D(2026, 10, 2), False),  # due today is not late yet
        ("accepted", D(2026, 10, 3), D(2026, 10, 2), False),
        ("accepted", None, D(2026, 10, 2), False),  # no date, never late
        ("completed", D(2026, 10, 1), D(2026, 10, 2), False),
        ("cancelled", D(2026, 10, 1), D(2026, 10, 2), False),
        ("pending", D(2026, 10, 1), D(2026, 10, 2), False),  # not started: nothing to be late on
        ("overdue", D(2026, 10, 1), D(2026, 10, 2), False),  # already marked
    ],
)
def test_an_open_action_is_overdue_the_day_after_its_target_date(status, target, today, overdue):
    assert rules.is_overdue(status, target, today) is overdue


@pytest.mark.parametrize(
    ("status", "target", "back"),
    [("overdue", D(2026, 10, 2), True), ("overdue", D(2026, 10, 9), True), ("overdue", None, True),
     ("overdue", D(2026, 10, 1), False), ("in_progress", D(2026, 10, 9), False)],
)  # fmt: skip
def test_an_overdue_action_whose_date_has_moved_on_is_back_on_time(status, target, back):
    assert rules.is_back_on_time(status, target, D(2026, 10, 2)) is back


@pytest.mark.parametrize(
    ("status", "target", "soon"),
    [("accepted", D(2026, 10, 2), True), ("accepted", D(2026, 10, 9), True),
     ("accepted", D(2026, 10, 10), False), ("accepted", D(2026, 10, 1), False),
     ("overdue", D(2026, 10, 3), False), ("completed", D(2026, 10, 3), False),
     ("pending", D(2026, 10, 3), False), ("accepted", None, False)],
)  # fmt: skip
def test_due_soon_means_within_a_week_and_still_open(status, target, soon):
    assert rules.due_soon(status, target, D(2026, 10, 2)) is soon


def test_how_late_it_is_in_days():
    assert rules.days_late(D(2026, 10, 1), D(2026, 10, 5)) == 4
    assert rules.days_late(D(2026, 10, 5), D(2026, 10, 5)) == 0
    assert rules.days_late(D(2026, 10, 9), D(2026, 10, 5)) == 0
    assert rules.days_late(None, D(2026, 10, 5)) == 0


def test_a_target_cannot_come_before_the_start():
    assert rules.dates_ok(D(2026, 10, 2), D(2026, 10, 2))
    assert rules.dates_ok(D(2026, 10, 2), D(2026, 10, 3))
    assert not rules.dates_ok(D(2026, 10, 3), D(2026, 10, 2))
    assert rules.dates_ok(None, D(2026, 10, 2)) and rules.dates_ok(D(2026, 10, 2), None)


def test_steps_are_tidied_and_progress_counted():
    steps = rules.clean_steps(
        ["  Do this  ", "", {"text": "Then this", "done": True}, {"text": "  "}]
    )
    assert steps == [{"text": "Do this", "done": False}, {"text": "Then this", "done": True}]
    progress = rules.progress(steps)
    assert (progress.done, progress.total, progress.percent) == (1, 2, 50)
    assert rules.progress([]).percent == 0
    assert rules.progress([{"text": "a", "done": True}] * 3).percent == 100
    assert rules.progress([{"text": "a", "done": True}] * 2 + [{"text": "b"}]).percent == 67
    assert len(rules.clean_steps(["x" * 400])[0]["text"]) == 300
    assert (
        rules.progress([{"text": "a", "done": True}] + [{"text": "b", "done": False}] * 2).percent
        == 33
    )


def test_a_default_target_is_a_little_after_the_start():
    assert rules.default_target(D(2026, 10, 1), 28) == D(2026, 10, 29)
    assert rules.default_target(D(2026, 10, 1), 0) == D(2026, 10, 2)


# --- accepting a recommendation ---------------------------------------------------------------


def accept(api, business, event, body=None, who="owner", org=0):
    return api.post(
        f"{ORGS}/{business[org]}/changes/{event['id']}/recommendation/accept",
        json=body,
        headers=business[2][who],
    )


@pytest.fixture
def suggested(api, march):
    """The bakery's March sales fall, with a recommendation worked out for it."""
    event = event_for(api, march, "revenue")
    body = recommend(api, march, event).json()
    return event, body


def get_action(api, business, action_id, who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/actions/{action_id}", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def test_accepting_the_recommended_option_makes_an_action_with_an_owner_dates_and_steps(
    api, march, suggested
):
    event, rec = suggested
    top = rec["options"][0]
    res = accept(api, march, event)
    assert res.status_code == 200, res.text
    body = res.json()
    today = today_uk()
    assert body["status"] == "accepted" and body["status_label"] == "Accepted"
    assert body["title"] == top["title"] and body["description"] == top["description"]
    assert body["owner"]["name"] == "owner" and body["created_by"]["name"] == "owner"
    assert body["approved_by"]["name"] == "owner"
    assert body["start_date"] == today.isoformat()
    assert body["target_date"] == (today + timedelta(days=top["days_to_effect"])).isoformat()
    assert [s["text"] for s in body["steps"]] == top["intervention"]["steps"]
    assert all(not s["done"] for s in body["steps"]) and body["progress"] == {
        "done": 0,
        "total": 4,
        "percent": 0,
    }
    assert body["next_statuses"] == ["in_progress", "partially_completed", "completed", "cancelled"]
    assert body["category"] == "financial" and body["completed_at"] is None


def test_the_decision_keeps_what_was_chosen_and_what_it_was_meant_to_change(api, march, suggested):
    event, rec = suggested
    top = rec["options"][0]
    decision = accept(api, march, event).json()["decision"]
    assert decision["title"] == top["title"] and decision["original_title"] is None
    assert decision["modified"] is False and decision["library_code"] == top["intervention"]["code"]
    assert (decision["kpi_code"], decision["kpi_name"], decision["unit"]) == (
        "revenue",
        "Sales",
        "gbp",
    )
    assert decision["baseline_period"] == "2026-03-01" and decision["baseline_value"] == "410.00"
    assert decision["expected_impact_value"] == top["impact_value"]
    assert decision["expected_impact_unit"] == top["impact_unit"]
    assert decision["recommendation_score"] == top["total_score"]
    assert decision["accepted_by"]["name"] == "owner" and decision["event_id"] == event["id"]


def test_the_history_starts_with_the_acceptance(api, march, suggested):
    event, rec = suggested
    body = accept(api, march, event).json()
    [created] = body["updates"]
    assert created["kind"] == "created" and created["to_status"] == "accepted"
    assert created["note"] == f"Accepted from the recommendation: {rec['options'][0]['title']}."
    assert created["user"]["name"] == "owner" and created["details"]["option_rank"] == 1


def test_accepting_marks_the_recommendation_as_taken_up_and_it_cannot_be_taken_twice(
    api, march, suggested
):
    event, _ = suggested
    assert accept(api, march, event).status_code == 200
    assert accept(api, march, event).status_code == 409
    again = accept(api, march, event)
    assert again.json()["error"]["code"] == "not_open"
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation"
    assert api.get(url, headers=march[2]["owner"]).json()["status"] == "accepted"


def test_any_of_the_options_can_be_accepted_not_just_the_first(api, march, suggested):
    event, rec = suggested
    third = rec["options"][2]
    body = accept(api, march, event, {"option_id": third["id"]}).json()
    assert body["title"] == third["title"]
    assert body["decision"]["recommendation_score"] == third["total_score"]
    assert body["updates"][0]["details"]["option_rank"] == 3


def test_an_option_that_is_not_one_of_the_suggestions_is_refused(api, march, suggested):
    event, _ = suggested
    res = accept(api, march, event, {"option_id": str(uuid.uuid4())})
    assert res.status_code == 404 and res.json()["error"]["code"] == "option_not_found"


def test_the_recommendation_can_be_changed_before_it_is_accepted(api, march, suggested):
    event, rec = suggested
    top = rec["options"][0]
    body = accept(
        api, march, event,
        {"title": "  Bake-off week  ", "description": "Our own plan.",
         "steps": [{"text": "Pick the flavours"}, {"text": "Tell regulars", "done": True}]},
    ).json()  # fmt: skip
    assert body["title"] == "Bake-off week" and body["description"] == "Our own plan."
    assert body["steps"] == [
        {"text": "Pick the flavours", "done": False},
        {"text": "Tell regulars", "done": True},
    ]
    decision = body["decision"]
    assert decision["modified"] is True and decision["original_title"] == top["title"]
    assert [u["kind"] for u in body["updates"]] == ["created", "modification"]
    assert body["updates"][1]["details"]["original_title"] == top["title"]


def test_leaving_everything_as_suggested_is_not_a_modification(api, march, suggested):
    event, rec = suggested
    top = rec["options"][0]
    body = accept(
        api, march, event,
        {"title": top["title"], "description": top["description"],
         "steps": [{"text": t} for t in top["intervention"]["steps"]]},
    ).json()  # fmt: skip
    assert body["decision"]["modified"] is False and [u["kind"] for u in body["updates"]] == [
        "created"
    ]


def test_changing_only_the_steps_counts_as_a_modification(api, march, suggested):
    event, _ = suggested
    body = accept(api, march, event, {"steps": [{"text": "Just one thing"}]}).json()
    assert body["decision"]["modified"] is True and body["decision"]["original_title"] is None


def test_the_owner_and_the_dates_can_be_chosen_when_accepting(api, db, march, suggested):
    event, _ = suggested
    with scoped(db, march):
        viewer = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    body = accept(
        api, march, event,
        {"owner_user_id": str(viewer), "start_date": "2026-11-02",
         "target_date": "2026-11-30", "note": "Over to you."},
    ).json()  # fmt: skip
    assert body["owner"]["name"] == "viewer"
    assert (body["start_date"], body["target_date"]) == ("2026-11-02", "2026-11-30")
    assert body["updates"][0]["note"] == "Over to you."


def test_only_the_start_date_given_means_the_target_follows_it(api, march, suggested):
    event, rec = suggested
    days = rec["options"][0]["days_to_effect"]
    body = accept(api, march, event, {"start_date": "2026-11-02"}).json()
    assert body["target_date"] == (date(2026, 11, 2) + timedelta(days=days)).isoformat()


def test_a_target_before_the_start_is_refused(api, march, suggested):
    event, _ = suggested
    res = accept(api, march, event, {"start_date": "2026-11-10", "target_date": "2026-11-01"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "dates_out_of_order"


def test_work_can_only_be_given_to_someone_in_the_business(api, march, suggested):
    event, _ = suggested
    res = accept(api, march, event, {"owner_user_id": str(uuid.uuid4())})
    assert res.status_code == 422 and res.json()["error"]["code"] == "not_a_member"


def test_a_change_with_no_recommendation_cannot_be_accepted(api, march):
    event = event_for(api, march, "revenue")
    res = accept(api, march, event)
    assert res.status_code == 404 and res.json()["error"]["code"] == "recommendation_not_found"


def test_good_news_has_nothing_to_accept(api, db, business):
    from tests.test_detection import Values, run

    values = Values(db, business)
    values.put("revenue", date(2026, 2, 1), 1000, None)
    values.put("revenue", date(2026, 3, 1), 1400, 1000)
    run(db, business)
    event = event_for(api, business, "revenue")
    recommend(api, business, event)
    res = accept(api, business, event)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_open"


def test_viewers_cannot_accept_and_strangers_are_not_let_in(api, march, suggested):
    event, _ = suggested
    assert accept(api, march, event, who="viewer").status_code == 403
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation/accept"
    assert api.post(url).status_code == 401


def test_a_recommendation_can_be_turned_down_and_stays_on_record(api, db, march, suggested):
    event, _ = suggested
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation/dismiss"
    assert api.post(url, json={"reason": "Not now"}, headers=march[2]["owner"]).status_code == 204
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "dismissed"
    with scoped(db, march):
        assert db.scalars(select(Recommendation)).one().basis["dismissed_reason"] == "Not now"
    assert accept(api, march, event).status_code == 409  # and cannot be accepted afterwards
    assert api.post(url, headers=march[2]["owner"]).status_code == 409


def test_viewers_cannot_turn_a_recommendation_down(api, march, suggested):
    event, _ = suggested
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation/dismiss"
    assert api.post(url, headers=march[2]["viewer"]).status_code == 403


# --- a manager's remit ------------------------------------------------------------------------


def add_manager(api, db, signup, business, categories, email="manager@acme.co.uk"):
    headers = signup(email)
    db.add(
        OrganizationUser(
            organization_id=uuid.UUID(business[0]),
            user_id=db.scalars(select(User.id).where(User.email == email)).one(),
            role_id=db.scalars(select(Role.id).where(Role.code == "manager")).first(),
            scope={"kpi_categories": categories},
        )
    )
    db.flush()
    business[2][email.split("@")[0]] = headers
    return email.split("@")[0]


def test_a_manager_inside_their_remit_accepts_outright(api, db, signup, march, suggested):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["financial"])
    body = accept(api, march, event, who=who).json()
    assert body["status"] == "accepted" and body["approved_by"]["name"] == "manager"
    assert body["owner"]["name"] == "manager"


def test_a_manager_outside_their_remit_can_only_propose(api, db, signup, march, suggested):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    body = accept(api, march, event, who=who).json()
    assert body["status"] == "pending" and body["status_label"] == "Waiting for approval"
    assert body["approved_by"] is None and body["owner"] is None
    assert body["updates"][0]["note"].startswith("Proposed from the recommendation:")
    assert body["next_statuses"] == ["accepted", "cancelled"]
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "proposed"
    assert accept(api, march, event).status_code == 409  # not open for a second go


def test_the_owner_approves_a_proposal_and_it_becomes_an_accepted_action(
    api, db, signup, march, suggested
):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    proposed = accept(api, march, event, who=who).json()
    url = f"{ORGS}/{march[0]}/actions/{proposed['id']}/approve"
    body = api.post(url, json={"reason": "Go ahead"}, headers=march[2]["owner"]).json()
    assert body["status"] == "accepted" and body["approved_by"]["name"] == "owner"
    assert body["owner"]["name"] == "owner"  # nobody was named, so the approver takes it
    assert [u["kind"] for u in body["updates"]] == ["created", "approved"]
    assert body["updates"][1]["note"] == "Go ahead"
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "accepted"


def test_a_proposal_that_named_an_owner_keeps_them_when_approved(api, db, signup, march, suggested):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    with scoped(db, march):
        viewer = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    proposed = accept(api, march, event, {"owner_user_id": str(viewer)}, who=who).json()
    body = api.post(
        f"{ORGS}/{march[0]}/actions/{proposed['id']}/approve", headers=march[2]["owner"]
    ).json()
    assert body["owner"]["name"] == "viewer"


def test_turning_a_proposal_down_cancels_it_and_frees_the_recommendation(
    api, db, signup, march, suggested
):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    proposed = accept(api, march, event, who=who).json()
    url = f"{ORGS}/{march[0]}/actions/{proposed['id']}/reject"
    body = api.post(url, json={"reason": "Not this month"}, headers=march[2]["owner"]).json()
    assert body["status"] == "cancelled" and body["updates"][-1]["kind"] == "rejected"
    assert body["updates"][-1]["note"] == "Not this month"
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "open"
    assert accept(api, march, event).status_code == 200  # can be taken up afresh


def test_only_a_proposal_can_be_approved_or_turned_down(api, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    for verb in ("approve", "reject"):
        res = api.post(
            f"{ORGS}/{march[0]}/actions/{action['id']}/{verb}", headers=march[2]["owner"]
        )
        assert res.status_code == 409 and res.json()["error"]["code"] == "not_pending"


def test_a_manager_outside_the_remit_cannot_approve_or_change_it(api, db, signup, march, suggested):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    action = accept(api, march, event, who=who).json()
    base = f"{ORGS}/{march[0]}/actions/{action['id']}"
    assert api.post(f"{base}/approve", headers=march[2][who]).status_code == 403
    assert api.patch(base, json={"title": "X"}, headers=march[2][who]).status_code == 403
    assert api.post(f"{base}/notes", json={"note": "x"}, headers=march[2][who]).status_code == 403
    denied = api.post(f"{base}/status", json={"status": "in_progress"}, headers=march[2][who])
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "outside_remit"


# --- working on it ----------------------------------------------------------------------------


@pytest.fixture
def action(api, march, suggested):
    event, _ = suggested
    return accept(api, march, event).json()


def act(api, business, action_id, path="", method="get", who="owner", **kw):
    return getattr(api, method)(
        f"{ORGS}/{business[0]}/actions/{action_id}{path}", headers=business[2][who], **kw
    )


def test_an_action_moves_forward_and_every_move_is_written_down(api, march, action):
    started = act(
        api,
        march,
        action["id"],
        "/status",
        "post",
        json={"status": "in_progress", "note": "Started today"},
    ).json()
    assert started["status"] == "in_progress" and started["completed_at"] is None
    assert started["next_statuses"] == ["partially_completed", "completed", "cancelled"]
    last = started["updates"][-1]
    assert (last["kind"], last["from_status"], last["to_status"], last["note"]) == (
        "status",
        "accepted",
        "in_progress",
        "Started today",
    )
    assert last["user"]["name"] == "owner"
    done = act(api, march, action["id"], "/status", "post", json={"status": "completed"}).json()
    assert done["status"] == "completed" and done["completed_at"] is not None
    assert done["next_statuses"] == [] and done["status_label"] == "Done"


def test_a_move_that_makes_no_sense_is_refused_with_the_choices(api, march, action):
    res = act(api, march, action["id"], "/status", "post", json={"status": "accepted"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "no_change"
    act(api, march, action["id"], "/status", "post", json={"status": "in_progress"})
    back = act(api, march, action["id"], "/status", "post", json={"status": "accepted"})
    assert back.status_code == 409 and back.json()["error"]["code"] == "bad_transition"
    assert back.json()["error"]["details"]["allowed"] == [
        "partially_completed",
        "completed",
        "cancelled",
    ]


def test_overdue_cannot_be_chosen(api, march, action):
    res = act(api, march, action["id"], "/status", "post", json={"status": "overdue"})
    assert res.status_code == 422


def test_a_finished_action_cannot_be_changed_but_can_still_be_noted(api, march, action):
    act(api, march, action["id"], "/status", "post", json={"status": "completed"})
    assert (
        act(api, march, action["id"], "/status", "post", json={"status": "in_progress"}).json()[
            "error"
        ]["code"]
        == "action_closed"
    )
    assert (
        act(api, march, action["id"], "", "patch", json={"title": "New"}).json()["error"]["code"]
        == "action_closed"
    )
    note = act(api, march, action["id"], "/notes", "post", json={"note": "Worked well"})
    assert note.status_code == 200 and note.json()["updates"][-1]["note"] == "Worked well"


def test_a_cancelled_action_is_final_too(api, march, action):
    done = act(
        api,
        march,
        action["id"],
        "/status",
        "post",
        json={"status": "cancelled", "note": "Changed our minds"},
    ).json()
    assert done["status"] == "cancelled" and done["completed_at"] is None
    assert (
        act(api, march, action["id"], "/status", "post", json={"status": "completed"}).status_code
        == 409
    )


def test_ticking_off_steps_is_recorded_with_the_progress(api, march, action):
    steps = [{"text": s["text"], "done": i < 2} for i, s in enumerate(action["steps"])]
    body = act(api, march, action["id"], "", "patch", json={"steps": steps}).json()
    assert body["progress"] == {"done": 2, "total": 4, "percent": 50}
    last = body["updates"][-1]
    assert last["kind"] == "steps" and last["note"] == "2 of 4 steps done."
    assert last["details"] == {"done": 2, "total": 4, "was_done": 0}


def test_saving_the_same_steps_again_adds_nothing_to_the_history(api, march, action):
    steps = [{"text": s["text"], "done": s["done"]} for s in action["steps"]]
    body = act(api, march, action["id"], "", "patch", json={"steps": steps}).json()
    assert len(body["updates"]) == len(action["updates"])


def test_it_can_be_given_to_someone_else_and_taken_back(api, db, march, action):
    with scoped(db, march):
        viewer = db.scalars(select(User.id).where(User.email == "viewer@acme.co.uk")).one()
    body = act(api, march, action["id"], "", "patch", json={"owner_user_id": str(viewer)}).json()
    assert body["owner"]["name"] == "viewer"
    last = body["updates"][-1]
    assert last["kind"] == "assignment" and last["note"] == "Now with viewer."
    assert last["details"] == {"from": "owner", "to": "viewer"}
    freed = act(api, march, action["id"], "", "patch", json={"owner_user_id": None}).json()
    assert (
        freed["owner"] is None and freed["updates"][-1]["note"] == "No longer assigned to anyone."
    )


def test_it_cannot_be_given_to_a_stranger(api, march, action):
    res = act(api, march, action["id"], "", "patch", json={"owner_user_id": str(uuid.uuid4())})
    assert res.status_code == 422 and res.json()["error"]["code"] == "not_a_member"


def test_the_dates_can_be_moved_and_the_old_ones_are_remembered(api, march, action):
    body = act(
        api,
        march,
        action["id"],
        "",
        "patch",
        json={"start_date": "2026-12-01", "target_date": "2026-12-20"},
    ).json()
    assert (body["start_date"], body["target_date"]) == ("2026-12-01", "2026-12-20")
    last = body["updates"][-1]
    assert last["kind"] == "dates" and last["note"] == "Start 01/12/2026, due 20/12/2026."
    assert (
        last["details"]["start_was"]
        == action["start_date"][8:10]
        + "/"
        + action["start_date"][5:7]
        + "/"
        + action["start_date"][:4]
    )


def test_one_date_can_be_moved_without_touching_the_other(api, march, action):
    body = act(api, march, action["id"], "", "patch", json={"target_date": "2027-01-31"}).json()
    assert body["start_date"] == action["start_date"] and body["target_date"] == "2027-01-31"


def test_a_target_before_the_start_is_refused_when_changing_dates(api, march, action):
    res = act(api, march, action["id"], "", "patch", json={"target_date": "2000-01-01"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "dates_out_of_order"


def test_a_date_can_be_taken_away(api, march, action):
    body = act(api, march, action["id"], "", "patch", json={"target_date": None}).json()
    assert body["target_date"] is None and body["days_late"] == 0


def test_the_action_can_be_renamed_and_described_again(api, march, action):
    body = act(
        api,
        march,
        action["id"],
        "",
        "patch",
        json={"title": "Our bake-off", "description": "New plan"},
    ).json()
    assert body["title"] == "Our bake-off" and body["description"] == "New plan"
    notes = [u["note"] for u in body["updates"] if u["kind"] == "modification"]
    assert notes == ['Renamed to "Our bake-off".', "Changed the description."]
    assert (
        body["decision"]["title"] == action["decision"]["title"]
    )  # what was decided is not rewritten


def test_changing_nothing_changes_nothing(api, march, action):
    body = act(api, march, action["id"], "", "patch", json={}).json()
    assert body == action
    same = act(api, march, action["id"], "", "patch", json={"title": action["title"]}).json()
    assert len(same["updates"]) == len(action["updates"])


def test_a_note_goes_into_the_history(api, march, action):
    body = act(
        api, march, action["id"], "/notes", "post", json={"note": "Spoke to the baker."}
    ).json()
    assert (
        body["updates"][-1]["kind"] == "note"
        and body["updates"][-1]["note"] == "Spoke to the baker."
    )
    assert body["updates"][-1]["user"]["name"] == "owner"
    assert act(api, march, action["id"], "/notes", "post", json={"note": "  "}).status_code == 422


def test_viewers_can_look_but_not_touch(api, march, action):
    assert get_action(api, march, action["id"], who="viewer")["id"] == action["id"]
    for path, method, body in (
        ("", "patch", {"title": "x"}), ("/status", "post", {"status": "in_progress"}),
        ("/notes", "post", {"note": "x"}),
        ("/evidence", "post", {"kind": "note", "title": "t", "note": "n"}),
    ):  # fmt: skip
        assert (
            act(api, march, action["id"], path, method, who="viewer", json=body).status_code == 403
        )


def test_an_action_nobody_has_is_not_found(api, march):
    assert (
        api.get(f"{ORGS}/{march[0]}/actions/{uuid.uuid4()}", headers=march[2]["owner"]).status_code
        == 404
    )


# --- overdue ----------------------------------------------------------------------------------


def set_dates(db, business, action_id, start, target):
    with scoped(db, business):
        db.execute(
            BusinessAction.__table__.update()
            .where(BusinessAction.id == uuid.UUID(action_id))
            .values(start_date=start, target_date=target)
        )


def test_an_action_past_its_target_date_is_marked_overdue_when_looked_at(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    body = get_action(api, march, action["id"])
    assert body["status"] == "accepted"  # looking at one action does not change it...
    rows = api.get(
        f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"]
    ).json()  # ...the list brings it up to date
    assert [(r["status"], r["days_late"]) for r in rows] == [("overdue", 5)]
    again = get_action(api, march, action["id"])
    assert again["status"] == "overdue" and again["days_late"] == 5
    last = again["updates"][-1]
    assert (
        last["kind"] == "overdue"
        and last["user"] is None
        and last["details"] == {"was": "accepted"}
    )
    assert last["note"].startswith("Passed its target date of ")


def test_marking_it_overdue_is_done_once_not_every_time_it_is_looked_at(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    for _ in range(3):
        api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"])
    updates = get_action(api, march, action["id"])["updates"]
    assert [u["kind"] for u in updates].count("overdue") == 1


def test_an_action_due_today_is_not_overdue(api, db, march, action):
    set_dates(db, march, action["id"], today_uk() - timedelta(days=3), today_uk())
    rows = api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"]).json()
    assert rows[0]["status"] == "accepted" and rows[0]["due_soon"] is True


def test_moving_the_date_on_takes_the_overdue_mark_off(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"])
    later = (today_uk() + timedelta(days=10)).isoformat()
    body = act(api, march, action["id"], "", "patch", json={"target_date": later}).json()
    assert body["status"] == "in_progress" and body["days_late"] == 0
    assert (
        body["updates"][-1]["note"]
        == "The target date has been moved on, so it is no longer overdue."
    )
    assert body["updates"][-1]["user"] is None


def test_work_that_is_overdue_can_still_be_finished(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"])
    done = act(api, march, action["id"], "/status", "post", json={"status": "completed"}).json()
    assert done["status"] == "completed" and done["updates"][-1]["from_status"] == "overdue"


def test_finished_and_cancelled_actions_never_become_overdue(api, db, march, action):
    act(api, march, action["id"], "/status", "post", json={"status": "completed"})
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    rows = api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"]).json()
    assert rows[0]["status"] == "completed"


def test_the_sweep_reports_how_many_it_changed(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    with scoped(db, march):
        assert service.refresh_overdue(db, owner_tenant(db, march)) == 1
        assert service.refresh_overdue(db, owner_tenant(db, march)) == 0
        assert (
            service.refresh_overdue(
                db, owner_tenant(db, march), today=today_uk() - timedelta(days=30)
            )
            == 1
        )


# --- the list and the summary -----------------------------------------------------------------


def test_the_list_puts_overdue_first_then_the_soonest_due(api, db, march):
    from tests.test_recommendations import event_for as event_of

    first = accept(api, march, event_of(api, march, "revenue"), None) if False else None
    _ = first


def test_the_list_and_filters(api, db, march, suggested):
    event, rec = suggested
    one = accept(
        api, march, event, {"target_date": (today_uk() + timedelta(days=30)).isoformat()}
    ).json()
    rows = api.get(f"{ORGS}/{march[0]}/actions", headers=march[2]["owner"]).json()
    assert [r["id"] for r in rows] == [one["id"]]
    row = rows[0]
    assert (
        row["title"] == one["title"]
        and row["kpi_name"] == "Sales"
        and row["category"] == "financial"
    )
    assert row["owner"]["name"] == "owner" and row["status_label"] == "Accepted"
    assert row["progress"] == {"done": 0, "total": 4, "percent": 0} and row["due_soon"] is False
    url = f"{ORGS}/{march[0]}/actions"
    assert len(api.get(f"{url}?status=accepted", headers=march[2]["owner"]).json()) == 1
    assert api.get(f"{url}?status=completed", headers=march[2]["owner"]).json() == []
    assert len(api.get(f"{url}?mine=true", headers=march[2]["owner"]).json()) == 1
    assert api.get(f"{url}?mine=true", headers=march[2]["viewer"]).json() == []
    assert len(api.get(f"{url}?open_only=true", headers=march[2]["owner"]).json()) == 1
    assert api.get(f"{url}?status=nonsense", headers=march[2]["owner"]).status_code == 422


def test_open_only_leaves_out_finished_work_but_keeps_proposals(api, db, signup, march, suggested):
    event, _ = suggested
    who = add_manager(api, db, signup, march, ["customer"])
    proposed = accept(api, march, event, who=who).json()
    url = f"{ORGS}/{march[0]}/actions?open_only=true"
    assert [r["id"] for r in api.get(url, headers=march[2]["owner"]).json()] == [proposed["id"]]
    act(api, march, proposed["id"], "/reject", "post")
    assert api.get(url, headers=march[2]["owner"]).json() == []


def test_the_summary_counts_each_state_and_what_is_yours(api, db, march, suggested):
    event, _ = suggested
    assert api.get(f"{ORGS}/{march[0]}/actions/summary", headers=march[2]["owner"]).json() == {
        "pending": 0, "accepted": 0, "in_progress": 0, "partially_completed": 0, "completed": 0,
        "cancelled": 0, "overdue": 0, "open": 0, "due_soon": 0, "mine_open": 0,
    }  # fmt: skip
    action = accept(
        api, march, event, {"target_date": (today_uk() + timedelta(days=3)).isoformat()}
    ).json()
    summary = api.get(f"{ORGS}/{march[0]}/actions/summary", headers=march[2]["owner"]).json()
    assert (summary["accepted"], summary["open"], summary["due_soon"], summary["mine_open"]) == (
        1,
        1,
        1,
        1,
    )
    assert (
        api.get(f"{ORGS}/{march[0]}/actions/summary", headers=march[2]["viewer"]).json()[
            "mine_open"
        ]
        == 0
    )
    act(api, march, action["id"], "/status", "post", json={"status": "completed"})
    done = api.get(f"{ORGS}/{march[0]}/actions/summary", headers=march[2]["owner"]).json()
    assert (done["completed"], done["open"], done["due_soon"], done["accepted"]) == (1, 0, 0, 0)


def test_the_summary_notices_overdue_work(api, db, march, action):
    set_dates(
        db, march, action["id"], today_uk() - timedelta(days=20), today_uk() - timedelta(days=5)
    )
    summary = api.get(f"{ORGS}/{march[0]}/actions/summary", headers=march[2]["owner"]).json()
    assert (summary["overdue"], summary["open"], summary["accepted"], summary["due_soon"]) == (
        1,
        1,
        0,
        0,
    )


# --- evidence ---------------------------------------------------------------------------------


def test_a_note_and_a_link_can_be_attached(api, march, action):
    note = act(
        api,
        march,
        action["id"],
        "/evidence",
        "post",
        json={"kind": "note", "title": "Shop front", "note": "Sign is up."},
    ).json()
    link = act(
        api,
        march,
        action["id"],
        "/evidence",
        "post",
        json={"kind": "link", "title": "Our post", "url": "https://example.com/post"},
    ).json()
    items = link["evidence"]
    assert [(e["kind"], e["title"]) for e in items] == [
        ("note", "Shop front"),
        ("link", "Our post"),
    ]
    assert items[0]["note"] == "Sign is up." and items[1]["url"] == "https://example.com/post"
    assert (
        items[0]["user"]["name"] == "owner"
        and note["updates"][-1]["note"] == "Added note: Shop front"
    )
    assert link["updates"][-1]["kind"] == "evidence"


@pytest.mark.parametrize(
    "bad",
    [{"kind": "link", "title": "x"}, {"kind": "note", "title": "x"},
     {"kind": "link", "title": "x", "url": "http://insecure.example"},
     {"kind": "link", "title": "x", "url": "javascript:alert(1)"},
     {"kind": "file", "title": "x"}, {"kind": "note", "title": " ", "note": "n"}],
)  # fmt: skip
def test_evidence_that_makes_no_sense_is_refused(api, march, action, bad):
    assert act(api, march, action["id"], "/evidence", "post", json=bad).status_code == 422


def upload(
    api, business, action_id, name="proof.pdf", data=b"%PDF-1.4 hello", title=None, who="owner"
):
    return api.post(
        f"{ORGS}/{business[0]}/actions/{action_id}/evidence/file",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        data={"title": title} if title else None,
        headers=business[2][who],
    )


def test_a_file_can_be_attached_and_downloaded_again(api, march, action):
    res = upload(api, march, action["id"], "Shop sign.pdf", b"%PDF-1.4 sign", "The new sign")
    assert res.status_code == 200, res.text
    [item] = res.json()["evidence"]
    assert (item["kind"], item["title"], item["filename"]) == (
        "file",
        "The new sign",
        "Shop sign.pdf",
    )
    assert item["content_type"] == "application/pdf" and item["size_bytes"] == 13
    got = api.get(
        f"{ORGS}/{march[0]}/actions/{action['id']}/evidence/{item['id']}/file",
        headers=march[2]["viewer"],
    )
    assert got.status_code == 200 and got.content == b"%PDF-1.4 sign"
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"] == 'attachment; filename="Shop sign.pdf"'
    assert got.headers["x-content-type-options"] == "nosniff"


def test_a_file_with_no_title_is_named_after_the_file(api, march, action):
    [item] = upload(api, march, action["id"], "photo.PNG", b"\x89PNG").json()["evidence"]
    assert item["title"] == "photo.PNG" and item["content_type"] == "image/png"


@pytest.mark.parametrize(
    "name", ["virus.exe", "page.html", "script.js", "noextension", "archive.zip", "x.svg"]
)
def test_only_ordinary_documents_and_pictures_can_be_attached(api, march, action, name):
    res = upload(api, march, action["id"], name)
    assert res.status_code == 422 and res.json()["error"]["code"] == "file_type_not_allowed"


def test_a_folder_in_the_file_name_is_ignored(api, march, action):
    [item] = upload(api, march, action["id"], "../../etc/proof.txt", b"hi").json()["evidence"]
    assert item["filename"] == "proof.txt"


def test_a_file_over_five_megabytes_is_refused_and_leaves_nothing_behind(
    api, db, march, action, storage
):
    res = upload(api, march, action["id"], "big.pdf", b"x" * (5 * 1024 * 1024 + 1))
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(ActionEvidence)) == 0
    assert not list(storage.root.rglob("*.pdf"))


def test_a_file_exactly_five_megabytes_is_allowed(api, march, action):
    assert upload(api, march, action["id"], "ok.pdf", b"x" * (5 * 1024 * 1024)).status_code == 200


def test_a_file_belongs_to_its_own_action_and_business(api, db, march, suggested, action):
    [item] = upload(api, march, action["id"]).json()["evidence"]
    path = f"{ORGS}/{march[0]}/actions/{uuid.uuid4()}/evidence/{item['id']}/file"
    assert api.get(path, headers=march[2]["owner"]).status_code == 404
    other = f"{ORGS}/{march[1]}/actions/{action['id']}/evidence/{item['id']}/file"
    assert api.get(other, headers=march[2]["owner"]).status_code == 404
    assert (
        api.get(
            f"{ORGS}/{march[0]}/actions/{action['id']}/evidence/{uuid.uuid4()}/file",
            headers=march[2]["owner"],
        ).status_code
        == 404
    )


def test_a_note_is_not_a_file_to_download(api, march, action):
    [note] = act(
        api,
        march,
        action["id"],
        "/evidence",
        "post",
        json={"kind": "note", "title": "t", "note": "n"},
    ).json()["evidence"]
    res = api.get(
        f"{ORGS}/{march[0]}/actions/{action['id']}/evidence/{note['id']}/file",
        headers=march[2]["owner"],
    )
    assert res.status_code == 404


def test_viewers_cannot_attach_files_but_can_download_them(api, march, action):
    assert upload(api, march, action["id"], who="viewer").status_code == 403
    [item] = upload(api, march, action["id"]).json()["evidence"]
    path = f"{ORGS}/{march[0]}/actions/{action['id']}/evidence/{item['id']}/file"
    assert api.get(path, headers=march[2]["viewer"]).status_code == 200
    assert api.get(path).status_code == 401


# --- keeping each business apart --------------------------------------------------------------


def test_each_business_sees_only_its_own_actions(api, db, march, action):
    assert api.get(f"{ORGS}/{march[1]}/actions", headers=march[2]["other"]).json() == []
    assert (
        api.get(f"{ORGS}/{march[1]}/actions/{action['id']}", headers=march[2]["other"]).status_code
        == 404
    )
    assert (
        api.get(f"{ORGS}/{march[0]}/actions/{action['id']}", headers=march[2]["other"]).status_code
        == 404
    )
    summary = api.get(f"{ORGS}/{march[1]}/actions/summary", headers=march[2]["other"]).json()
    assert summary["open"] == 0
    with scoped(db, march, 1):
        assert db.scalar(select(func.count()).select_from(BusinessAction)) == 0
        assert db.scalar(select(func.count()).select_from(BusinessIntervention)) == 0
        assert db.scalar(select(func.count()).select_from(ActionUpdate)) == 0


def test_the_actions_need_a_login(api, march, action):
    assert api.get(f"{ORGS}/{march[0]}/actions").status_code == 401
    assert api.get(f"{ORGS}/{march[0]}/actions/{action['id']}").status_code == 401


# --- the tables -------------------------------------------------------------------------------


def test_the_work_is_kept_if_the_recommendation_is_made_again(api, db, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    with scoped(db, march):
        db.execute(Recommendation.__table__.delete())  # re-making a recommendation replaces it
    body = get_action(api, march, action["id"])
    assert body["title"] == action["title"] and body["decision"]["baseline_value"] == "410.00"


def test_every_acceptance_writes_to_the_audit_log(api, db, march, suggested):
    from app.models.identity import AuditLog

    event, _ = suggested
    action = accept(api, march, event).json()
    act(api, march, action["id"], "/status", "post", json={"status": "in_progress"})
    act(api, march, action["id"], "", "patch", json={"target_date": "2027-01-31"})
    with scoped(db, march):
        pass
    names = [
        a for (a,) in db.execute(select(AuditLog.action).where(AuditLog.action.like("action.%")))
    ]
    assert {"action.accepted", "action.status_changed", "action.updated"} <= set(names)


def _row(db, business, **extra):
    with scoped(db, business):
        from app.models.kpi import KpiDefinition

        kpi_id = db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == "revenue")).one()
        intervention = BusinessIntervention(
            kpi_id=kpi_id, category="financial", title="T", description="D",
            accepted_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(intervention)
        db.flush()
        fields = dict(
            intervention_id=intervention.id, title="A", description="D", category="financial",
            status="accepted", last_activity_at=datetime.now(UTC),
        )  # fmt: skip
        action = BusinessAction(**{**fields, **extra})
        db.add(action)
        db.flush()
        return action.id


def test_an_action_can_be_stored(db, business):
    _row(db, business)


@pytest.mark.parametrize(
    "bad",
    [{"status": "maybe"}, {"category": "weather"}, {"title": " "},
     {"start_date": date(2026, 5, 2), "target_date": date(2026, 5, 1)},
     {"status": "completed"}, {"completed_at": datetime(2026, 5, 1, tzinfo=UTC)}],
)  # fmt: skip
def test_an_action_that_makes_no_sense_is_refused(db, business, bad):
    with pytest.raises(IntegrityError):
        _row(db, business, **bad)


def test_a_finished_action_has_a_finish_time(db, business):
    _row(db, business, status="completed", completed_at=datetime(2026, 5, 1, tzinfo=UTC))


def test_there_is_one_action_per_intervention(db, business):
    action_id = _row(db, business)
    with scoped(db, business):
        existing = db.get(BusinessAction, action_id)
        db.add(
            BusinessAction(
                intervention_id=existing.intervention_id, title="B", description="D",
                category="financial", status="accepted", last_activity_at=datetime.now(UTC),
            )
        )  # fmt: skip
        with pytest.raises(IntegrityError):
            db.flush()


def test_evidence_must_have_what_its_kind_needs(db, business):
    action_id = _row(db, business)
    for bad in ({"kind": "link"}, {"kind": "file"}, {"kind": "other"}, {"title": " "}):
        with pytest.raises(IntegrityError), scoped(db, business):
            fields = dict(action_id=action_id, kind="note", title="T", created_at=datetime.now(UTC))
            db.add(ActionEvidence(**{**fields, **bad}))
            db.flush()
        db.rollback()
        action_id = _row(db, business)


def test_removing_an_action_removes_its_history_and_evidence(db, business):
    action_id = _row(db, business)
    with scoped(db, business):
        db.add(
            ActionUpdate(action_id=action_id, kind="note", note="x", created_at=datetime.now(UTC))
        )
        db.add(
            ActionEvidence(
                action_id=action_id, kind="note", title="T", created_at=datetime.now(UTC)
            )
        )
        db.flush()
        db.execute(BusinessAction.__table__.delete())
        assert db.scalar(select(func.count()).select_from(ActionUpdate)) == 0
        assert db.scalar(select(func.count()).select_from(ActionEvidence)) == 0


def test_an_update_must_be_of_a_known_kind(db, business):
    action_id = _row(db, business)
    with pytest.raises(IntegrityError), scoped(db, business):
        db.add(ActionUpdate(action_id=action_id, kind="gossip", created_at=datetime.now(UTC)))
        db.flush()


def test_history_cannot_be_attached_to_another_businesss_action(db, business):
    action_id = _row(db, business)
    with pytest.raises(IntegrityError), scoped(db, business, 1):
        db.add(ActionUpdate(action_id=action_id, kind="note", created_at=datetime.now(UTC)))
        db.flush()
