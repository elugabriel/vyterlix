# ruff: noqa: E501, F811
"""Business memory: what is normal, what has been tried, and the owner's limits."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.memory import rules
from app.models.actions import BusinessIntervention
from app.models.memory import (
    BusinessLearning,
    BusinessMemory,
    InterventionPattern,
    MemoryRetrievalEvent,
)
from app.services import memory, track_record
from tests.test_detection import Values
from tests.test_health import ORGS, owner_tenant, scoped
from tests.test_outcomes import check, done, figures, finish, suggested  # noqa: F401
from tests.test_recommendations import event_for, recommend

# --- what is normal -----------------------------------------------------------------------------


def test_a_band_of_normal_needs_half_a_year_of_figures():
    assert rules.normal_range([D(100)] * 5) is None
    assert rules.normal_range([D(100)] * 6) is not None


def test_what_is_normal_is_the_average_and_one_standard_deviation_either_side():
    band = rules.normal_range([D(100), D(120), D(80), D(100), D(100), D(100)])
    assert band.months == 6 and band.mean == 100
    assert round(band.low, 2) == D("88.45") and round(band.high, 2) == D("111.55")


def test_only_the_last_twelve_months_count():
    old_and_new = [D(1000)] * 5 + [D(100)] * 12
    band = rules.normal_range(old_and_new)
    assert (
        band.months == 12 and band.mean == 100 and band.low == band.high == 100
    )  # flat is still a band here


def test_a_count_is_never_normally_below_nothing():
    values = [D(0), D(0), D(0), D(0), D(0), D(60)]
    assert rules.normal_range(values).low < 0
    assert rules.normal_range(values, count=True).low == 0


def test_a_figure_is_below_within_or_above_what_is_normal():
    band = rules.NormalRange(12, D(100), D(90), D(110))
    assert [rules.position(D(v), band) for v in (89, 90, 100, 110, 111)] == [
        "below", "within", "within", "within", "above",
    ]  # fmt: skip


# --- the owner's limits ---------------------------------------------------------------------------


def limit(**kw):
    return rules.broken_limit(
        kw.pop("c", rules.Constraints()), code=kw.pop("code", "x"), effort=kw.pop("effort", "low"),
        cost_level=kw.pop("cost", "none"), days=kw.pop("days", 14),
    )  # fmt: skip


def test_with_no_limits_nothing_is_ruled_out():
    assert not rules.Constraints().any
    assert limit(effort="high", cost="high", days=365) is None


def test_an_action_the_owner_ruled_out_is_left_out():
    c = rules.Constraints(excluded_actions={"discount"})
    assert limit(c=c, code="discount") == "you asked us not to suggest this kind of action"
    assert limit(c=c, code="other") is None


@pytest.mark.parametrize(
    ("cost", "ruled_out"), [("none", False), ("low", False), ("medium", True), ("high", True)]
)
def test_the_cost_limit_is_inclusive(cost, ruled_out):
    c = rules.Constraints(max_cost_level="low")
    assert (limit(c=c, cost=cost) is not None) is ruled_out


@pytest.mark.parametrize(
    ("effort", "ruled_out"), [("low", False), ("medium", False), ("high", True)]
)
def test_the_effort_limit_is_inclusive(effort, ruled_out):
    c = rules.Constraints(max_effort="medium")
    assert (limit(c=c, effort=effort) is not None) is ruled_out


@pytest.mark.parametrize(("days", "ruled_out"), [(1, False), (30, False), (31, True), (90, True)])
def test_quick_results_only_means_showing_within_a_month(days, ruled_out):
    c = rules.Constraints(quick_results_only=True)
    assert (limit(c=c, days=days) is not None) is ruled_out
    assert limit(c=rules.Constraints(), days=days) is None


def test_the_reason_names_the_limit_that_was_broken():
    c = rules.Constraints(max_cost_level="low", max_effort="low", quick_results_only=True)
    assert "costs more than you said you can spend (high cost, your limit is low)" in limit(
        c=c, cost="high"
    )
    assert "takes more effort than you said you can give (high effort, your limit is low)" in limit(
        c=c, effort="high"
    )
    assert "only want quick results and it takes about 45 days" in limit(c=c, days=45)


# --- patterns, lessons and cases ---------------------------------------------------------------------


def test_the_busiest_and_quietest_days_come_from_sales_per_weekday():
    best, worst, best_share, worst_share = rules.busiest_days(
        {0: D(100), 1: D(300), 5: D(100), 6: D(0)}
    )
    assert (best, worst) == (1, 0)  # a tie goes to the earlier day
    assert best_share == 60 and worst_share == 20
    assert rules.busiest_days({0: D(100)}) is None and rules.busiest_days({}) is None
    assert rules.busiest_days({0: D(0), 1: D(0)}) is None


def test_a_share_of_nothing_is_not_a_share():
    assert rules.share(D(1), D(0)) is None and rules.share(D(1), D(4)) == 25


def test_a_lesson_is_a_sentence_for_each_kind_of_result():
    args = dict(action="Run a special", kpi_name="Sales", achieved_pct=50, reason="R.")
    assert rules.lesson(outcome="successful", **args) == '"Run a special" worked for Sales: R.'
    assert (
        rules.lesson(outcome="partially_successful", **args)
        == '"Run a special" helped Sales only in part: R.'
    )
    assert (
        rules.lesson(outcome="unsuccessful", **args) == '"Run a special" did not work for Sales: R.'
    )
    assert (
        rules.lesson(outcome="inconclusive", **args)
        == 'We could not tell whether "Run a special" worked for Sales: R.'
    )


def test_the_average_achieved_is_taken_over_the_results_that_have_one():
    assert rules.average_achieved([80, 40]) == 60
    assert rules.average_achieved([80, None, 40, None]) == 60
    assert rules.average_achieved([None]) is None and rules.average_achieved([]) is None


def test_similar_cases_are_on_the_same_figure_and_the_ones_that_worked_come_first():
    cases = [
        rules.Case("a", "revenue", "unsuccessful", 5, "u"),
        rules.Case("b", "revenue", "successful", 120, "s1"),
        rules.Case("c", "gross_profit", "successful", 300, "other figure"),
        rules.Case("d", "revenue", "inconclusive", None, "says nothing"),
        rules.Case("e", "revenue", "successful", 200, "s2"),
        rules.Case("f", "revenue", "partially_successful", 50, "p"),
    ]
    assert [c.lesson for c in rules.similar_cases(cases, "revenue")] == ["s2", "s1", "u"]
    assert rules.similar_cases(cases, "nothing") == []
    assert rules.MAX_CASES == 3


def test_the_record_on_this_figure_beats_the_record_elsewhere():
    here = {"a": track_record.Record(successful=2), "b": track_record.Record(inconclusive=1)}
    general = {
        "a": track_record.Record(unsuccessful=3),
        "b": track_record.Record(successful=1),
        "c": track_record.Record(unsuccessful=1),
    }
    recalled = memory.Recall(rules.Constraints(), here)
    assert recalled.history("a", general) == 83  # two successes here, whatever happened elsewhere
    assert recalled.history("b", general) == 75  # nothing decided here, so the business-wide record
    assert recalled.history("c", general) == 25
    assert recalled.history("d", general) is None


# --- reading and rebuilding ---------------------------------------------------------------------------


def rebuild(api, business, who="owner", org=0):
    return api.post(f"{ORGS}/{business[org]}/memory/rebuild", headers=business[2][who])


def read(api, business, who="owner", org=0):
    return api.get(f"{ORGS}/{business[org]}/memory", headers=business[2][who]).json()


def test_a_new_business_remembers_nothing_and_has_no_limits(api, business):
    body = read(api, business)
    assert (
        body["normal_ranges"]
        == body["customer_patterns"]
        == body["lessons"]
        == body["patterns"]
        == []
    )
    assert body["recent_use"] == [] and body["last_rebuilt"] is None
    assert body["constraints"] == {
        "max_cost_level": None, "max_effort": None, "excluded_actions": [], "excluded_names": [],
        "quick_results_only": False, "updated_at": None,
    }  # fmt: skip


def test_what_is_normal_is_worked_out_for_each_figure_with_enough_months(api, db, business):
    Values(db, business).series("revenue", [100, 120, 80, 100, 100, 100])
    Values(db, business).series("gross_profit", [10, 20, 30])  # too few months
    res = rebuild(api, business)
    assert res.status_code == 200 and res.json()["normal_ranges"] == 1
    [item] = read(api, business)["normal_ranges"]
    assert (
        item["key"] == "range:revenue"
        and item["source"] == "derived"
        and item["title"] == "What is normal for Sales"
    )
    assert item["statement"] == (
        "Sales is usually between £88.45 and £111.55 a month (it averaged £100.00 over the last 6 months). "
        "The latest month, June 2026, was £100.00, within what is usual."
    )
    assert item["data"]["position"] == "within" and item["data"]["months"] == 6


def test_a_latest_month_outside_the_band_is_called_so(api, db, business):
    Values(db, business).series("revenue", [100, 100, 100, 100, 100, 100, 40])
    rebuild(api, business)
    [item] = read(api, business)["normal_ranges"]
    assert item["data"]["position"] == "below" and item["statement"].endswith(
        "was £40.00, below what is usual."
    )


def test_a_figure_that_never_moves_has_no_band(api, db, business):
    Values(db, business).series("revenue", [100] * 6)
    assert rebuild(api, business).json()["normal_ranges"] == 0


def test_incomplete_or_failed_months_are_not_counted(api, db, business):
    values = Values(db, business)
    values.series("revenue", [100, 110, 90, 100, 105, 95])
    values.put("revenue", date(2026, 7, 1), 9999, None, complete=False)
    values.put("revenue", date(2026, 8, 1), None, None, status="no_data")
    rebuild(api, business)
    [item] = read(api, business)["normal_ranges"]
    assert item["data"]["months"] == 6 and item["data"]["latest"] == "95.00"


def test_patterns_in_customers_come_from_the_sales_records(api, bakery):
    res = rebuild(api, bakery)
    assert res.json()["customer_patterns"] == 3
    items = {i["key"]: i for i in read(api, bakery)["customer_patterns"]}
    assert (
        items["weekdays"]["statement"]
        == "Tuesday is your busiest day (52% of sales) and Saturday your quietest (5%)."
    )
    assert items["weekdays"]["data"]["busiest"] == "Tuesday"
    assert (
        items["repeat_customers"]["statement"]
        == "2 of your 2 named customers (100%) have bought more than once."
    )
    assert (
        items["top_products"]["statement"]
        == "Your three best sellers (Sourdough, Cake, Brownie) bring in 100% of your sales."
    )


def test_refunds_are_not_counted_as_sales_in_the_patterns(api, bakery):
    rebuild(api, bakery)
    weekdays = {i["key"]: i for i in read(api, bakery)["customer_patterns"]}["weekdays"]
    assert (
        weekdays["data"]["busiest_share"] == "52.3"
    )  # 800 of 1530; the Friday refund adds nothing


def test_goals_and_seasons_are_remembered_and_forgotten_when_they_stop_applying(api, db, business):
    headers = business[2]["owner"]
    goal = api.post(
        f"{ORGS}/{business[0]}/goals",
        json={
            "title": "Grow sales",
            "goal_type": "increase_revenue",
            "kpi_code": "revenue",
            "target_value": "20000",
            "target_unit": "gbp",
            "priority": 2,
        },
        headers=headers,
    )
    assert goal.status_code == 201, goal.text
    season = api.post(
        f"{ORGS}/{business[0]}/seasons",
        json={
            "name": "Christmas",
            "start": {"month": 12, "day": 1},
            "end": {"month": 1, "day": 5},
            "expected_change_pct": "40",
        },
        headers=headers,
    )
    assert season.status_code == 201, season.text
    assert rebuild(api, business).json()["goals"] == 1
    body = read(api, business)
    assert body["goals"][0]["statement"].startswith(
        "Goal (priority 2 of 5): Grow sales, aiming for £20,000.00"
    )
    assert (
        "Christmas" in body["seasons"][0]["statement"]
        and "40% busier" in body["seasons"][0]["statement"]
    )
    api.patch(
        f"{ORGS}/{business[0]}/goals/{goal.json()['id']}",
        json={"status": "abandoned"},
        headers=headers,
    )
    again = rebuild(api, business).json()
    assert again["goals"] == 0 and again["removed"] == 1
    assert read(api, business)["goals"] == [] and len(read(api, business)["seasons"]) == 1


def test_rebuilding_twice_changes_nothing_and_never_touches_the_owners_limits(api, db, business):
    Values(db, business).series("revenue", [100, 110, 90, 100, 105, 95])
    headers = business[2]["owner"]
    api.put(f"{ORGS}/{business[0]}/memory/constraints", json={"max_effort": "low"}, headers=headers)
    first = rebuild(api, business).json()
    second = rebuild(api, business).json()
    assert first == second and second["removed"] == 0
    assert read(api, business)["constraints"]["max_effort"] == "low"
    with scoped(db, business):
        assert (
            db.scalar(
                select(func.count())
                .select_from(BusinessMemory)
                .where(BusinessMemory.kind == "normal_range")
            )
            == 1
        )


def test_who_may_read_rebuild_and_set_limits(api, business):
    assert read(api, business, "viewer")["lessons"] == []
    assert api.get(f"{ORGS}/{business[0]}/memory").status_code == 401
    assert rebuild(api, business, "viewer").status_code == 403
    assert (
        api.put(
            f"{ORGS}/{business[0]}/memory/constraints", json={}, headers=business[2]["viewer"]
        ).status_code
        == 403
    )


# --- the owner's limits through the API --------------------------------------------------------------------


def put_limits(api, business, body, who="owner"):
    return api.put(f"{ORGS}/{business[0]}/memory/constraints", json=body, headers=business[2][who])


def test_the_owners_limits_are_saved_and_replaced_as_a_whole(api, business):
    res = put_limits(
        api,
        business,
        {
            "max_cost_level": "low",
            "max_effort": "medium",
            "excluded_actions": ["bundle_offer", "promote_product"],
            "quick_results_only": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["max_cost_level"], body["max_effort"], body["quick_results_only"]) == (
        "low",
        "medium",
        True,
    )
    assert (
        body["excluded_actions"] == ["bundle_offer", "promote_product"]
        and len(body["excluded_names"]) == 2
    )
    assert body["updated_at"] is not None
    cleared = put_limits(api, business, {}).json()
    assert (
        cleared["max_cost_level"] is None
        and cleared["excluded_actions"] == []
        and cleared["quick_results_only"] is False
    )
    shown = api.get(
        f"{ORGS}/{business[0]}/memory/constraints", headers=business[2]["viewer"]
    ).json()
    assert shown == cleared


def test_the_limits_are_written_in_plain_english_in_memory(api, db, business):
    put_limits(
        api,
        business,
        {"max_cost_level": "none", "max_effort": "high", "excluded_actions": ["bundle_offer"]},
    )
    with scoped(db, business):
        rows = {
            r.key: r.statement
            for r in db.scalars(select(BusinessMemory).where(BusinessMemory.source == "owner"))
        }
    assert rows["max_cost_level"] == "An action may cost nothing, no more."
    assert rows["max_effort"] == "An action may take a lot of effort, no more."
    assert rows["excluded_actions"].startswith("Never suggest: ")
    assert rows["quick_results_only"] == "Slower actions are fine."


@pytest.mark.parametrize(
    "bad",
    [
        {"max_cost_level": "huge"},
        {"max_effort": "none"},
        {"excluded_actions": ["nonsense"]},
        {"surprise": 1},
        {"quick_results_only": "maybe"},
    ],
)
def test_limits_that_make_no_sense_are_refused(api, business, bad):
    assert put_limits(api, business, bad).status_code == 422


def test_a_second_business_has_its_own_limits(api, business):
    put_limits(api, business, {"max_effort": "low"})
    assert (
        api.get(f"{ORGS}/{business[1]}/memory/constraints", headers=business[2]["other"]).json()[
            "max_effort"
        ]
        is None
    )


# --- limits shape the recommendation ---------------------------------------------------------------------------


@pytest.fixture
def options(api, march):
    event = event_for(api, march, "revenue")
    return event, recommend(api, march, event).json()


def shown(api, march, event):
    return api.get(
        f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation", headers=march[2]["owner"]
    ).json()


def test_an_action_the_owner_ruled_out_is_left_out_and_the_recommendation_says_so(
    api, march, options
):
    event, rec = options
    top = rec["options"][0]
    put_limits(api, march, {"excluded_actions": [top["intervention"]["code"]]})
    again = recommend(api, march, event).json()
    assert top["intervention"]["code"] not in [o["intervention"]["code"] for o in again["options"]]
    lines = [e["statement"] for e in again["evidence"]]
    assert (
        f'"{top["intervention"]["name"]}" was left out because you asked us not to suggest this kind of action.'
        in lines
    )


def test_the_cost_limit_removes_the_costlier_actions(api, march, options):
    event, rec = options
    assert {o["cost_level"] for o in rec["options"]} != {"none"}
    put_limits(api, march, {"max_cost_level": "none"})
    again = recommend(api, march, event).json()
    assert all(o["cost_level"] == "none" for o in again["options"])
    assert any(
        "costs more than you said you can spend" in e["statement"] for e in again["evidence"]
    )


def test_the_effort_limit_removes_the_harder_actions(api, march, options):
    event, rec = options
    put_limits(api, march, {"max_effort": "low"})
    again = recommend(api, march, event).json()
    assert all(o["effort"] == "low" for o in again["options"])


def test_quick_results_only_removes_the_slow_actions(api, march, options):
    event, rec = options
    put_limits(api, march, {"quick_results_only": True})
    again = recommend(api, march, event).json()
    assert again["options"] and all(o["days_to_effect"] <= 30 for o in again["options"])


def test_when_every_answer_breaks_a_limit_the_recommendation_says_that(api, march, options):
    event, rec = options
    all_codes = [
        i["code"]
        for i in api.get(f"{ORGS}/{march[0]}/interventions", headers=march[2]["owner"]).json()
    ]
    assert len(all_codes) == 10
    put_limits(api, march, {"excluded_actions": all_codes})
    again = recommend(api, march, event).json()
    assert again["status"] == "insufficient_evidence" and again["options"] == []
    assert "breaks a limit you set" in again["rationale"]


def test_what_memory_was_used_is_written_down_and_shown(api, db, march, options):
    event, rec = options
    put_limits(api, march, {"max_effort": "low"})
    recommend(api, march, event)
    recent = read(api, march)["recent_use"]
    assert (
        recent and recent[0]["purpose"] == "recommendation" and recent[0]["event_id"] == event["id"]
    )
    kinds = {u["kind"] for u in recent[0]["used"]}
    assert "limits" in kinds and "limit" in kinds


def test_a_recommendation_that_used_no_memory_leaves_no_record(api, db, march, options):
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(MemoryRetrievalEvent)) == 0


# --- learning from results ----------------------------------------------------------------------------------------


def learned(db, march, action, value=100000):
    figures(db, march, action, value)
    return check(db, march, action)


def test_a_result_leaves_a_lesson_and_a_pattern(api, db, march, done):
    learned(db, march, done)
    body = read(api, march)
    [lesson] = body["lessons"]
    assert lesson["outcome"] == "successful" and lesson["kpi_code"] == "revenue"
    assert lesson["lesson"].startswith(f'"{done["title"]}" worked for Sales: It won back')
    [pattern] = body["patterns"]
    assert (
        pattern["code"],
        pattern["kpi_code"],
        pattern["successful"],
        pattern["unsuccessful"],
    ) == (done["decision"]["library_code"], "revenue", 1, 0)
    assert pattern["last_outcome"] == "successful" and pattern["average_achieved_pct"] is not None
    assert pattern["action"]


def test_learning_from_one_result_twice_counts_it_once(api, db, march, done):
    learned(db, march, done)
    with scoped(db, march):
        memory.learn(db, owner_tenant(db, march), db.scalars(select(BusinessIntervention.id)).one())
        assert db.scalar(select(func.count()).select_from(BusinessLearning)) == 1
        assert db.scalars(select(InterventionPattern)).one().successful == 1


def test_a_pattern_is_always_just_what_the_lessons_say(api, db, march, done):
    learned(db, march, done)
    with scoped(db, march):
        pattern = db.scalars(select(InterventionPattern)).one()
        pattern.successful, pattern.unsuccessful = 7, 4  # something stale
        db.flush()
        lesson = db.scalars(select(BusinessLearning)).one()
        db.delete(lesson)
        db.flush()
        memory.learn(db, owner_tenant(db, march), db.scalars(select(BusinessIntervention.id)).one())
        pattern = db.scalars(select(InterventionPattern)).one()
        assert (pattern.successful, pattern.unsuccessful) == (1, 0)
        assert pattern.average_achieved_pct is not None and pattern.last_outcome == "successful"
        assert pattern.last_measured_at is not None


def test_inconclusive_results_are_kept_but_do_not_change_the_average(api, db, march, done):
    figures(db, march, done, 5000, quality=10)
    check(db, march, done)
    with scoped(db, march):
        pattern = db.scalars(select(InterventionPattern)).one()
    assert (pattern.inconclusive, pattern.successful, pattern.average_achieved_pct) == (1, 0, None)
    assert read(api, march)["lessons"][0]["lesson"].startswith("We could not tell whether")


def test_a_result_that_said_nothing_is_not_reported_as_a_try(api, db, march, suggested, done):
    event, _ = suggested
    figures(db, march, done, 5000, quality=10)
    check(db, march, done)
    again = recommend(api, march, event).json()
    assert not any(
        "has been tried" in e["statement"]
        and "Last time" not in e["statement"]
        and "partly worked and" in e["statement"]
        and "On Sales" in e["statement"]
        for e in again["evidence"]
    )
    assert not any(
        e["statement"].startswith("Last time: ") for e in again["evidence"]
    )  # inconclusive says nothing


def test_the_next_recommendation_remembers_what_happened_last_time(api, db, march, suggested, done):
    event, _ = suggested
    learned(db, march, done)
    again = recommend(api, march, event).json()
    lines = [e["statement"] for e in again["evidence"]]
    assert any(line.startswith("Last time: ") and "worked for Sales" in line for line in lines)
    assert any(
        "has been tried 1 time in your business: 1 worked, 0 partly worked and 0 did not." in line
        for line in lines
    )
    recent = read(api, march)["recent_use"][0]
    assert {u["kind"] for u in recent["used"]} >= {"case", "pattern"}


def test_a_failure_is_remembered_too_and_the_action_is_not_suggested_again_for_that_cause(
    api, db, march, suggested
):
    event, rec = suggested
    action = finish(api, march, event)
    learned(db, march, action, value=100)
    again = shown(api, march, event)
    tried = action["decision"]["library_code"]
    assert all(
        not (o["intervention"]["code"] == tried and o["target"] == action["decision"]["target"])
        for o in again["options"]
    )
    lines = [e["statement"] for e in again["evidence"]]
    assert any(
        line.startswith("Last time: ") and "did not work for Sales" in line for line in lines
    )


# --- keeping each business apart and the tables ------------------------------------------------------------------


def test_one_business_never_sees_anothers_memory(api, db, business):
    Values(db, business).series("revenue", [100, 110, 90, 100, 105, 95])
    rebuild(api, business)
    other = api.get(f"{ORGS}/{business[1]}/memory", headers=business[2]["other"]).json()
    assert other["normal_ranges"] == []
    with scoped(db, business, 1):
        assert db.scalar(select(func.count()).select_from(BusinessMemory)) == 0


def test_a_fact_has_one_row_per_business_kind_and_key(db, business):
    with scoped(db, business):
        db.add(BusinessMemory(kind="goal", key="k", title="t", statement="s", source="derived"))
        db.flush()
        db.add(BusinessMemory(kind="goal", key="k", title="t2", statement="s2", source="derived"))
        with pytest.raises(IntegrityError):
            db.flush()


@pytest.mark.parametrize("bad", [{"kind": "rumour"}, {"source": "guess"}, {"key": " "}])
def test_a_fact_that_makes_no_sense_is_refused(db, business, bad):
    fields = dict(kind="goal", key="k", title="t", statement="s", source="derived")
    with scoped(db, business), pytest.raises(IntegrityError):
        db.add(BusinessMemory(**{**fields, **bad}))
        db.flush()


def test_a_pattern_cannot_have_negative_counts_or_be_stored_twice(db, business):
    with scoped(db, business):
        db.add(InterventionPattern(library_code="a", kpi_code="revenue", successful=-1))
        with pytest.raises(IntegrityError):
            db.flush()


def test_one_pattern_per_action_and_figure(db, business):
    with scoped(db, business):
        db.add(InterventionPattern(library_code="a", kpi_code="revenue"))
        db.flush()
        db.add(InterventionPattern(library_code="a", kpi_code="revenue"))
        with pytest.raises(IntegrityError):
            db.flush()


def test_a_lesson_must_have_a_known_outcome(db, business):
    with scoped(db, business), pytest.raises(IntegrityError):
        db.add(
            BusinessLearning(
                intervention_id=uuid.uuid4(), kpi_code="revenue", outcome="great", lesson="l"
            )
        )
        db.flush()


def test_removing_the_intervention_removes_its_lesson(db, march, done):
    learned(db, march, done)
    with scoped(db, march):
        from app.models.actions import BusinessAction

        db.execute(BusinessAction.__table__.delete())
        db.execute(BusinessIntervention.__table__.delete())
        assert db.scalar(select(func.count()).select_from(BusinessLearning)) == 0


def test_a_retrieval_event_keeps_what_was_used(db, business):
    with scoped(db, business):
        db.add(
            MemoryRetrievalEvent(
                purpose="recommendation",
                used=[{"kind": "case", "statement": "s"}],
                created_at=datetime.now(UTC),
            )
        )
        db.flush()
        assert db.scalars(select(MemoryRetrievalEvent)).one().used[0]["kind"] == "case"
