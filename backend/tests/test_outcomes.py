# ruff: noqa: E501
"""Follow-up and outcomes: when a finished action is checked, and whether it worked."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.core.uk import today_uk
from app.models.actions import BusinessIntervention
from app.models.business import NotificationPreference
from app.models.identity import User
from app.models.outcomes import FollowUpSchedule, InterventionOutcome
from app.outcomes import rules
from app.services import outcomes, scheduler, track_record
from tests.test_actions import accept, act
from tests.test_detection import Values
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_recommendations import event_for, recommend


def gbp(value):
    return outcomes.show(value, "gbp")


def history(option):
    return next(line["score"] for line in option["scores"] if line["key"] == "history")


# --- when to look -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("finished", "first_full"),
    [(date(2026, 3, 15), date(2026, 4, 1)), (date(2026, 3, 1), date(2026, 4, 1)),
     (date(2026, 3, 31), date(2026, 4, 1)), (date(2026, 12, 20), date(2027, 1, 1))],
)  # fmt: skip
def test_the_first_full_month_is_the_one_after_the_work_finished(finished, first_full):
    assert rules.first_full_month(finished) == first_full


@pytest.mark.parametrize(
    ("finished", "days", "due"),
    [
        (date(2026, 3, 15), 7, date(2026, 5, 1)),  # a full month must pass whatever the speed
        (date(2026, 3, 15), 28, date(2026, 5, 1)),
        (date(2026, 3, 15), 90, date(2026, 6, 13)),  # a slow action waits for its time
        (date(2026, 12, 20), 14, date(2027, 2, 1)),
        (date(2026, 3, 1), 0, date(2026, 5, 1)),
    ],
)
def test_the_follow_up_waits_for_the_action_to_show_and_a_full_month_to_pass(finished, days, due):
    assert rules.follow_up_due(finished, days) == due


@pytest.mark.parametrize(
    ("due", "month"),
    [(date(2026, 5, 1), date(2026, 4, 1)), (date(2026, 6, 13), date(2026, 5, 1)),
     (date(2027, 1, 1), date(2026, 12, 1)), (date(2027, 2, 1), date(2027, 1, 1)),
     (date(2026, 5, 31), date(2026, 4, 1))],
)  # fmt: skip
def test_the_month_measured_is_the_latest_one_that_had_finished_by_the_due_date(due, month):
    assert rules.month_to_measure(due) == month


def test_the_month_measured_never_comes_before_the_first_full_month():
    for day in range(1, 29):
        for month in range(1, 13):
            for days in (0, 7, 30, 120):
                finished = date(2026, month, day)
                due = rules.follow_up_due(finished, days)
                assert rules.month_to_measure(due) >= rules.first_full_month(finished)
                assert due >= finished + timedelta(days=days)


def test_a_year_earlier_keeps_the_month():
    assert rules.a_year_earlier(date(2026, 3, 1)) == date(2025, 3, 1)


def test_an_improvement_is_positive_when_the_business_is_better_off():
    assert rules.improvement("up_good", D(100), D(130)) == 30
    assert rules.improvement("up_good", D(100), D(80)) == -20
    assert rules.improvement("down_good", D(100), D(80)) == 20
    assert rules.improvement("down_good", D(100), D(130)) == -30


@pytest.mark.parametrize(
    ("won", "partly", "lost", "score"),
    [(0, 0, 0, 50), (1, 0, 0, 75), (0, 0, 1, 25), (0, 1, 0, 50), (3, 0, 0, 88), (0, 0, 3, 12),
     (2, 1, 1, 60)],
)  # fmt: skip
def test_the_track_record_starts_neutral_and_moves_with_each_result(won, partly, lost, score):
    assert rules.history_score(won, partly, lost) == score


# --- the verdict --------------------------------------------------------------------------------


def judge(expected, actual, seasonal=None, quality=100):
    return rules.judge(
        expected=None if expected is None else D(expected), actual=D(actual),
        seasonal=None if seasonal is None else D(seasonal), data_quality=quality, show=gbp,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("actual", "outcome", "pct"),
    [(100, "successful", 100), (80, "successful", 80), (79, "partially_successful", 79),
     (30, "partially_successful", 30), (29, "unsuccessful", 29), (0, "unsuccessful", 0),
     (-50, "unsuccessful", -50), (250, "successful", 250)],
)  # fmt: skip
def test_the_verdict_comes_from_how_much_of_the_expected_change_was_achieved(actual, outcome, pct):
    verdict = judge(100, actual)
    assert (verdict.outcome, verdict.achieved_pct) == (outcome, pct)


def test_the_reason_says_what_was_expected_and_what_happened():
    assert judge(200, 200).reason == "It won back £200.00, against the £200.00 we expected (100%)."
    assert judge(200, 100).reason == (
        "It won back only £100.00, against the £200.00 we expected (50%)."
    )
    assert judge(200, -20).reason == (
        "It did not win anything back: the figure moved by -£20.00, against the £200.00 we expected."
    )


@pytest.mark.parametrize("expected", [None, 0, -5])
def test_with_nothing_expected_there_is_nothing_to_judge_by(expected):
    verdict = judge(expected, 500)
    assert verdict.outcome == "inconclusive" and "no expected result" in verdict.reason
    assert verdict.achieved_pct is None


def test_a_month_with_poor_data_is_not_trusted_but_the_limit_itself_is():
    bad = judge(100, 100, quality=59)
    assert bad.outcome == "inconclusive" and "59 out of 100" in bad.reason
    assert judge(100, 100, quality=60).outcome == "successful"
    assert judge(100, 100, quality=None).outcome == "successful"


def test_the_normal_change_for_the_time_of_year_is_taken_out_first():
    verdict = judge(100, 120, seasonal=30)
    assert verdict.adjusted == 90 and verdict.outcome == "successful"
    assert verdict.reason == (
        "It won back £90.00 after taking out £30.00 that the time of year brings, "
        "against the £100.00 we expected (90%)."
    )
    assert judge(100, 120, seasonal=60).outcome == "partially_successful"
    assert judge(100, 50, seasonal=-20).adjusted == 70  # a normal dip makes the result better


def test_when_the_season_alone_would_have_brought_the_change_it_is_inconclusive():
    verdict = judge(100, 90, seasonal=85)
    assert verdict.outcome == "inconclusive" and verdict.adjusted == 5
    assert "time of year" in verdict.reason and verdict.achieved_pct == 5
    # but a result well beyond the season still counts
    assert judge(100, 200, seasonal=85).outcome == "successful"
    # the limits are inclusive: exactly 80% of the expected change counts as explained, and a
    # result of exactly 80% counts as a success
    assert judge(100, 90, seasonal=80).outcome == "inconclusive"
    assert judge(100, 160, seasonal=80).outcome == "successful"
    # and a season that brings less than the limit does not hide a poor result
    assert judge(100, 90, seasonal=79).outcome == "unsuccessful"


# --- finishing an action and being followed up ----------------------------------------------------


@pytest.fixture
def suggested(api, march):
    event = event_for(api, march, "revenue")
    rec = recommend(api, march, event).json()
    return event, rec


def finish(api, march, event, body=None, who="owner"):
    action = accept(api, march, event, body, who=who).json()
    return act(
        api, march, action["id"], "/status", "post", json={"status": "completed"}, who=who
    ).json()


@pytest.fixture
def done(api, march, suggested):
    event, _ = suggested
    return finish(api, march, event)


def check(db, march, action, **kw):
    plan = action["follow_up"]
    when = kw.pop("today", date.fromisoformat(plan["due_date"]))
    with scoped(db, march):
        return outcomes.measure(
            db, owner_tenant(db, march), uuid.UUID(action["id"]), today=when, **kw
        )


def figures(db, march, action, value, quality=100, last_year=None):
    """The revenue the follow-up will read (and what the same months did a year earlier)."""
    values = Values(db, march)
    month = date.fromisoformat(action["follow_up"]["measure_month"])
    values.put("revenue", month, value, None, quality=quality)
    if last_year:
        base = date.fromisoformat(action["decision"]["baseline_period"])
        values.put("revenue", rules.a_year_earlier(base), last_year[0], None)
        values.put("revenue", rules.a_year_earlier(month), last_year[1], None)


def test_finishing_an_action_plans_its_follow_up(api, march, suggested, done):
    _, rec = suggested
    days = rec["options"][0]["days_to_effect"]
    plan = done["follow_up"]
    today = today_uk()
    expected_due = rules.follow_up_due(today, days)
    assert plan["due_date"] == expected_due.isoformat()
    assert plan["measure_month"] == rules.month_to_measure(expected_due).isoformat()
    assert plan["status"] == "scheduled" and plan["is_due"] is False and plan["notified"] is False
    assert done["outcome"] is None


def test_an_action_that_is_not_finished_has_no_follow_up(api, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    assert action["follow_up"] is None and action["outcome"] is None
    act(api, march, action["id"], "/status", "post", json={"status": "partially_completed"})
    assert act(api, march, action["id"]).json()["follow_up"] is None
    act(api, march, action["id"], "/status", "post", json={"status": "cancelled"})
    assert act(api, march, action["id"]).json()["follow_up"] is None


def test_it_is_too_soon_to_check_before_the_date(api, march, done):
    res = act(api, march, done["id"], "/measure", "post")
    assert res.status_code == 409 and res.json()["error"]["code"] == "too_early"
    assert res.json()["error"]["details"] == {"due_date": done["follow_up"]["due_date"]}


def test_only_a_finished_action_can_be_checked(api, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    res = act(api, march, action["id"], "/measure", "post")
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_finished"


def test_viewers_cannot_ask_for_a_check_but_can_read_the_report(api, march, done):
    assert act(api, march, done["id"], "/measure", "post", who="viewer").status_code == 403
    assert act(api, march, done["id"], "/report", who="viewer").status_code == 200
    assert api.get(f"{ORGS}/{march[0]}/actions/{done['id']}/report").status_code == 401


def test_a_successful_action_is_recorded_with_the_figures(api, db, march, done):
    decision = done["decision"]
    expected = D(decision["expected_impact_value"])
    figures(db, march, done, D(decision["baseline_value"]) + expected)
    result = check(db, march, done)
    assert result.outcome == "successful" and result.label == "It worked"
    assert result.kpi_name == "Sales" and result.unit == "gbp"
    assert (result.baseline_value, result.measured_value) == (
        decision["baseline_value"], str(D(decision["baseline_value"]) + expected),
    )  # fmt: skip
    assert result.expected_change == str(expected) and result.actual_change == str(expected)
    assert result.seasonal_change is None and result.achieved_pct == 100
    assert result.baseline_period == date(2026, 3, 1)
    assert result.measured_period == date.fromisoformat(done["follow_up"]["measure_month"])
    assert result.measured_by == "owner"  # asked for by a person, not the timed round
    assert result.alternative is None
    again = act(api, march, done["id"]).json()
    assert again["outcome"]["outcome"] == "successful" and again["follow_up"]["status"] == "done"
    assert again["updates"][-1]["kind"] == "outcome" and again["updates"][-1]["user"] is None
    assert again["updates"][-1]["note"].startswith("It worked. It won back")


def test_the_check_is_done_once(api, db, march, done):
    figures(db, march, done, 1000)
    check(db, march, done)
    with pytest.raises(ConflictError) as error:
        check(db, march, done)
    assert error.value.code == "already_measured"
    res = act(api, march, done["id"], "/measure", "post")
    assert res.status_code == 409


@pytest.mark.parametrize(
    ("change", "outcome"),
    [(1.0, "successful"), (0.5, "partially_successful"), (0.1, "unsuccessful"),
     (-1.0, "unsuccessful")],
)  # fmt: skip
def test_the_outcome_follows_how_much_of_the_expected_change_came(db, march, done, change, outcome):
    expected = D(done["decision"]["expected_impact_value"])
    figures(db, march, done, D(done["decision"]["baseline_value"]) + expected * D(str(change)))
    assert check(db, march, done).outcome == outcome


def test_a_figure_that_got_worse_counts_against_the_action(db, march, done):
    figures(db, march, done, 100)
    result = check(db, march, done)
    assert result.outcome == "unsuccessful"
    assert D(result.actual_change) == D(100) - D(done["decision"]["baseline_value"])
    assert "did not win anything back" in result.reason


def test_incomplete_data_makes_the_result_inconclusive(db, march, done):
    figures(db, march, done, 5000, quality=40)
    result = check(db, march, done)
    assert result.outcome == "inconclusive" and result.data_quality == 40
    assert "too incomplete" in result.reason


def test_last_years_figures_take_the_seasonal_change_out(db, march, done):
    expected = D(done["decision"]["expected_impact_value"])
    # the same two months a year earlier also rose by the whole expected amount: the season did it
    figures(
        db,
        march,
        done,
        D(done["decision"]["baseline_value"]) + expected,
        last_year=(1000, 1000 + expected),
    )
    result = check(db, march, done)
    assert result.outcome == "inconclusive" and result.seasonal_change == str(expected)
    assert result.adjusted_change == "0.00" and "time of year" in result.reason


def test_a_result_well_beyond_the_season_still_counts(db, march, done):
    expected = D(done["decision"]["expected_impact_value"])
    figures(
        db, march, done, D(done["decision"]["baseline_value"]) + expected * 3,
        last_year=(1000, 1000 + expected),
    )  # fmt: skip
    result = check(db, march, done)
    assert result.outcome == "successful" and result.achieved_pct == 200


def test_missing_figures_are_waited_for_and_then_given_up_on(db, march, done):
    due = date.fromisoformat(done["follow_up"]["due_date"])
    with pytest.raises(ConflictError) as error:
        check(db, march, done, today=due + timedelta(days=60))
    assert error.value.code == "no_figures_yet"
    result = check(db, march, done, today=due + timedelta(days=61))
    assert result.outcome == "inconclusive" and result.measured_value is None
    assert "still no figures" in result.reason


def test_an_incomplete_month_is_not_used(db, march, done):
    values = Values(db, march)
    month = date.fromisoformat(done["follow_up"]["measure_month"])
    values.put("revenue", month, 9999, None, complete=False)
    with pytest.raises(ConflictError):
        check(db, march, done)


def test_a_check_can_be_asked_for_from_the_screen_once_it_is_due(api, db, march, done):
    plan = done["follow_up"]
    with scoped(db, march):  # move the plan into the past, as if the time had gone by
        row = db.scalars(select(FollowUpSchedule)).one()
        row.due_date = today_uk() - timedelta(days=1)
        month = (today_uk().replace(day=1) - timedelta(days=40)).replace(day=1)
        row.measure_month = month
    Values(db, march).put("revenue", month, 2000, None)
    res = act(api, march, done["id"], "/measure", "post")
    assert res.status_code == 200, res.text
    assert res.json()["outcome"]["outcome"] == "successful" and plan["status"] == "scheduled"
    assert res.json()["outcome"]["measured_by"] == "owner"


# --- the round the worker makes -----------------------------------------------------------------


def make_due(db, march):
    """Pretend the follow-up date has come, and give it figures to read."""
    with scoped(db, march):
        row = db.scalars(select(FollowUpSchedule)).one()
        return row.due_date, row.measure_month


def test_the_sweep_tells_the_person_responsible_once_and_measures_when_the_figures_arrive(
    api, db, march, done, outbox
):
    due, month = make_due(db, march)
    outbox.clear()  # the sign-up emails
    with scoped(db, march):
        tenant = owner_tenant(db, march)
        assert outcomes.sweep(db, tenant, today=due - timedelta(days=1), sender=outbox) == {
            "due": 0, "told": 0, "measured": 0, "waiting": 0,
        }  # fmt: skip
        assert outbox == []
        assert outcomes.sweep(db, tenant, today=due, sender=outbox) == {
            "due": 1, "told": 1, "measured": 0, "waiting": 1,
        }  # fmt: skip
    [mail] = outbox
    assert mail.to == "owner@acme.co.uk" and "Time to check" in mail.subject
    assert done["title"] in mail.subject and f"{month:%B %Y}" in mail.body
    with scoped(db, march):  # nothing new: it does not tell them again
        assert outcomes.sweep(db, tenant, today=due, sender=outbox)["told"] == 0
    assert len(outbox) == 1
    expected = D(done["decision"]["expected_impact_value"])
    Values(db, march).put("revenue", month, D(done["decision"]["baseline_value"]) + expected, None)
    with scoped(db, march):
        assert outcomes.sweep(db, tenant, today=due, sender=outbox) == {
            "due": 1, "told": 0, "measured": 1, "waiting": 0,
        }  # fmt: skip
        assert outcomes.sweep(db, tenant, today=due, sender=outbox)["due"] == 0
    assert len(outbox) == 2 and outbox[1].subject == f'The result of "{done["title"]}"'
    assert outbox[1].body.startswith("It worked: It won back")
    body = act(api, march, done["id"]).json()
    assert body["outcome"]["measured_by"] is None and body["follow_up"]["notified"] is True


def test_a_person_who_has_switched_action_emails_off_is_not_emailed(db, march, done, outbox):
    due, _ = make_due(db, march)
    outbox.clear()
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        db.add(
            NotificationPreference(
                user_id=owner.id, category="action", email=False, in_app=True, push=True
            )
        )
        db.flush()
        result = outcomes.sweep(db, owner_tenant(db, march), today=due, sender=outbox)
    assert result["told"] == 1 and outbox == []  # still counted as followed up, just not emailed


def test_the_timed_round_marks_overdue_work_and_follows_up_in_every_business(
    db, march, done, outbox
):
    due, month = make_due(db, march)
    Values(db, march).put("revenue", month, 5000, None)
    totals = scheduler.tick(db, today=due)
    assert totals["businesses"] >= 1 and totals["measured"] == 1 and totals["followed_up"] == 1
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(InterventionOutcome)) == 1
    assert scheduler.tick(db, today=due)["measured"] == 0  # and again changes nothing


def test_a_check_asked_for_by_a_person_does_not_email_anyone(db, march, done, outbox):
    figures(db, march, done, 100000)
    outbox.clear()
    check(db, march, done, sender=outbox)
    assert outbox == []


def test_a_person_who_leaves_action_emails_on_still_gets_them(db, march, done, outbox):
    due, _ = make_due(db, march)
    outbox.clear()
    with scoped(db, march):
        owner = db.scalars(select(User).where(User.email == "owner@acme.co.uk")).one()
        db.add(
            NotificationPreference(
                user_id=owner.id, category="action", email=True, in_app=False, push=False
            )
        )
        db.flush()
        outcomes.sweep(db, owner_tenant(db, march), today=due, sender=outbox)
    assert len(outbox) == 1


def test_one_missing_year_earlier_figure_means_no_seasonal_adjustment(db, march, done):
    expected = D(done["decision"]["expected_impact_value"])
    values = Values(db, march)
    month = date.fromisoformat(done["follow_up"]["measure_month"])
    values.put("revenue", month, D(done["decision"]["baseline_value"]) + expected, None)
    values.put("revenue", rules.a_year_earlier(date(2026, 3, 1)), 1000, None)  # only one of the two
    result = check(db, march, done)
    assert result.seasonal_change is None and result.outcome == "successful"


# --- what to do next, and the track record ----------------------------------------------------------


def test_a_result_that_was_not_a_success_brings_a_different_suggestion(api, db, march, suggested):
    event, rec = suggested
    action = finish(api, march, event)
    figures(db, march, action, 100)
    tried = rec["options"][0]
    result = check(db, march, action)
    assert result.outcome == "unsuccessful" and result.alternative is not None
    assert result.alternative != tried["title"]
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "open" and shown["options"][0]["title"] == result.alternative
    assert tried["title"] not in [o["title"] for o in shown["options"]]
    assert act(api, march, action["id"]).json()["outcome"]["alternative"] == result.alternative


def test_a_partial_result_also_brings_a_different_suggestion(api, db, march, suggested):
    event, rec = suggested
    action = finish(api, march, event)
    expected = D(action["decision"]["expected_impact_value"])
    figures(db, march, action, D(action["decision"]["baseline_value"]) + expected / 2)
    result = check(db, march, action)
    assert result.outcome == "partially_successful" and result.alternative is not None
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert rec["options"][0]["title"] not in [o["title"] for o in shown["options"]]


def test_a_success_leaves_the_recommendation_alone(api, db, march, suggested, done):
    event, _ = suggested
    figures(db, march, done, 100000)
    assert check(db, march, done).alternative is None
    shown = api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()
    assert shown["status"] == "accepted"


def test_the_track_record_counts_each_kind_of_action(db, march, done):
    figures(db, march, done, 100000)
    check(db, march, done)
    with scoped(db, march):
        record = track_record.load(db)
    code = done["decision"]["library_code"]
    assert record[code].successful == 1 and record[code].decided == 1 and record[code].score == 75
    assert list(record) == [code]


def test_inconclusive_results_do_not_move_the_track_record(db, march, done):
    figures(db, march, done, 5000, quality=10)
    check(db, march, done)
    with scoped(db, march):
        record = track_record.load(db)[done["decision"]["library_code"]]
    assert (record.inconclusive, record.decided, record.score) == (1, 0, 50)


def test_a_good_record_lifts_that_kind_of_action_in_later_recommendations(
    api, db, march, suggested, done
):
    event, rec = suggested
    code = done["decision"]["library_code"]
    figures(db, march, done, 100000)
    check(db, march, done)
    again = recommend(api, march, event).json()
    boosted = [o for o in again["options"] if o["intervention"]["code"] == code]
    assert boosted and all(history(o) == 75 for o in boosted)
    others = [o for o in again["options"] if o["intervention"]["code"] != code]
    assert all(history(o) == 50 for o in others)
    evidence = [e["statement"] for e in again["evidence"]]
    assert any("tried 1 time in your business: 1 worked" in s for s in evidence)


def test_with_no_outcomes_the_track_record_is_neutral_as_before(api, march, suggested):
    _, rec = suggested
    assert {history(o) for o in rec["options"]} == {50}
    assert any("no record yet" in e["statement"] for e in rec["evidence"])


# --- the summary and the report ---------------------------------------------------------------------


def test_the_summary_counts_what_is_waiting_and_how_the_rest_turned_out(
    api, db, march, suggested, done
):
    url = f"{ORGS}/{march[0]}/actions/outcomes"
    empty = api.get(url, headers=march[2]["viewer"]).json()
    assert (empty["waiting"], empty["due"], empty["successful"], empty["track_record"]) == (
        1,
        0,
        0,
        [],
    )
    due, _ = make_due(db, march)
    with scoped(db, march):
        row = db.scalars(select(FollowUpSchedule)).one()
        row.due_date = today_uk()
    assert api.get(url, headers=march[2]["viewer"]).json()["due"] == 1
    figures(db, march, done, 100000)
    check(db, march, done)
    body = api.get(url, headers=march[2]["owner"]).json()
    assert (body["waiting"], body["due"], body["successful"]) == (0, 0, 1)
    [line] = body["track_record"]
    assert (line["code"], line["successful"], line["score"]) == (
        done["decision"]["library_code"],
        1,
        75,
    )
    assert line["name"]
    assert api.get(url).status_code == 401


def test_the_report_tells_the_whole_story(api, db, march, suggested):
    event, rec = suggested
    action = finish(api, march, event)
    act(api, march, action["id"], "/notes", "post", json={"note": "Went well"})
    act(
        api,
        march,
        action["id"],
        "/evidence",
        "post",
        json={"kind": "note", "title": "t", "note": "n"},
    )
    steps = [{"text": s["text"], "done": True} for s in action["steps"][:2]] + action["steps"][2:]
    act(api, march, action["id"], "", "patch", json={"steps": steps}, who="owner")
    before = act(api, march, action["id"], "/report").json()
    assert before["title"] == action["title"] and before["kpi_name"] == "Sales"
    assert before["accepted_by"] == "owner" and before["owner"] == "owner"
    assert (before["steps_done"], before["steps_total"]) == (0, 4) or before["steps_total"] == 4
    assert before["notes"][0].endswith(": Went well") and before["evidence_count"] == 1
    assert before["outcome"] is None and before["follow_up"]["status"] == "scheduled"
    assert before["why"] and "checked from" in before["summary"]
    assert before["expected_impact_value"] == rec["options"][0]["impact_value"]
    figures(db, march, action, 100000)
    check(db, march, action)
    after = act(api, march, action["id"], "/report").json()
    assert after["outcome"]["outcome"] == "successful" and "It worked." in after["summary"]


def test_the_report_of_unfinished_work_says_there_is_no_result_yet(api, march, suggested):
    event, _ = suggested
    action = accept(api, march, event).json()
    body = act(api, march, action["id"], "/report").json()
    assert "no result to measure yet" in body["summary"] and body["outcome"] is None
    assert act(api, march, uuid.uuid4(), "/report").status_code == 404


def test_other_businesses_see_nothing_of_it(api, db, march, done):
    assert (
        api.get(f"{ORGS}/{march[1]}/actions/outcomes", headers=march[2]["other"]).json()["waiting"]
        == 0
    )
    assert (
        api.get(
            f"{ORGS}/{march[1]}/actions/{done['id']}/report", headers=march[2]["other"]
        ).status_code
        == 404
    )
    with scoped(db, march, 1):
        assert db.scalar(select(func.count()).select_from(FollowUpSchedule)) == 0


# --- amounts in words ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [(D("1234.5"), "gbp", "£1,234.50"), (D("-5"), "gbp", "-£5.00"), (D("2.25"), "percent", "2.2 points"),
     (D("-1.5"), "percent", "-1.5 points"), (D("1200"), "count", "1,200"), (D("0.456"), "ratio", "0.46"),
     (None, "gbp", None)],
)  # fmt: skip
def test_amounts_are_written_in_the_figures_own_unit(value, unit, text):
    assert outcomes.show(value, unit) == text


# --- the tables -----------------------------------------------------------------------------------------


def stored(db, march, done):
    with scoped(db, march):
        return db.scalars(select(BusinessIntervention.id)).one()


def test_one_outcome_and_one_follow_up_per_action(db, march, done):
    intervention = stored(db, march, done)
    with scoped(db, march):
        db.add(
            InterventionOutcome(
                intervention_id=intervention, outcome="successful", reason="r",
                rules_version="x", measured_at=datetime.now(UTC),
            )
        )  # fmt: skip
        db.flush()
        db.add(
            InterventionOutcome(
                intervention_id=intervention, outcome="unsuccessful", reason="r",
                rules_version="x", measured_at=datetime.now(UTC),
            )
        )  # fmt: skip
        with pytest.raises(IntegrityError):
            db.flush()


@pytest.mark.parametrize("bad", [{"outcome": "great"}, {"data_quality": 101}, {"data_quality": -1}])
def test_an_outcome_that_makes_no_sense_is_refused(db, march, done, bad):
    intervention = stored(db, march, done)
    with scoped(db, march), pytest.raises(IntegrityError):
        fields = dict(intervention_id=intervention, outcome="successful", reason="r", rules_version="x",
                      measured_at=datetime.now(UTC))  # fmt: skip
        db.add(InterventionOutcome(**{**fields, **bad}))
        db.flush()


def test_removing_the_intervention_removes_its_follow_up_and_outcome(db, march, done):
    figures(db, march, done, 100000)
    check(db, march, done)
    with scoped(db, march):
        from app.models.actions import BusinessAction

        db.execute(BusinessAction.__table__.delete())
        db.execute(BusinessIntervention.__table__.delete())
        assert db.scalar(select(func.count()).select_from(InterventionOutcome)) == 0
        assert db.scalar(select(func.count()).select_from(FollowUpSchedule)) == 0
