"""Recommendations: the rules (pure), then whole recommendations on shops worked out by hand."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.diagnostics import DetectionEvent, Diagnosis
from app.models.kpi import KpiDefinition
from app.models.recommendations import (
    Intervention,
    Recommendation,
    RecommendationEvidence,
    RecommendationOption,
)
from app.recommend import rules
from app.services import recommendations as service
from tests.test_detection import Values, run
from tests.test_health import ORGS, scoped

D = Decimal


def finding(
    kind="sale_value", lens="orders", label=None, amount="-390", share="205.3", text="Said."
):
    return rules.Finding(
        kind, lens, label or kind, D(amount), None if share is None else D(share), text
    )


def action(
    code="a", kinds=(("sale_value", None),), kpis=("revenue",),
    goal_types=("increase_revenue",), effort="low", cost="none", days=14, share="0.2",
):  # fmt: skip
    return rules.Action(
        code, code.title(), f"{code} summary.", ["one"],
        [{"kind": k, **({"dimension": d} if d else {})} for k, d in kinds],
        list(kpis), list(goal_types), effort, cost, days, D(share),
    )  # fmt: skip


# --- which findings call up which actions -----------------------------------------------------


def test_the_weights_add_up_to_a_hundred():
    assert sum(rules.WEIGHTS.values()) == 100


@pytest.mark.parametrize(
    ("share", "hurting"),
    [("25", True), ("24.9", False), ("142.1", True), ("-80", False), (None, False)],
)
def test_a_finding_hurts_when_it_is_a_real_share_of_the_change_in_the_same_direction(
    share, hurting
):
    assert rules.hurts(finding(share=share)) is hurting


def test_the_part_a_finding_is_about_is_read_from_its_label():
    assert rules.part_of(finding("contributor", "parts", "Product: Sourdough")) == (
        "Product",
        "Sourdough",
    )
    assert rules.part_of(finding("contributor", "parts", "Day of the week: Tuesday")) == (
        "Day of the week",
        "Tuesday",
    )
    assert rules.part_of(finding("sale_value")) == (None, None)
    assert rules.part_of(finding("contributor", "parts", "No colon")) == (None, None)


def test_an_action_answers_the_kind_of_finding_and_the_figure_it_names():
    bundle = action(kinds=(("sale_value", None),))
    assert rules.matches(bundle, finding("sale_value"), "revenue")
    assert not rules.matches(bundle, finding("price"), "revenue")  # a different kind of cause
    assert not rules.matches(
        bundle, finding("sale_value"), "operating_expenses"
    )  # a different figure


def test_an_action_for_one_way_of_splitting_ignores_the_others():
    promote = action(kinds=(("contributor", "Product"),))
    assert rules.matches(promote, finding("contributor", "parts", "Product: Sourdough"), "revenue")
    assert not rules.matches(
        promote, finding("contributor", "parts", "Sales channel: Shop"), "revenue"
    )
    anything = action(kinds=(("contributor", None),))
    assert rules.matches(
        anything, finding("contributor", "parts", "Sales channel: Shop"), "revenue"
    )


def test_only_hurting_findings_call_up_actions_and_each_action_is_aimed_once_per_target():
    library = [action("bundle", kinds=(("sale_value", None), ("volume", None)))]
    found = rules.generate(
        library,
        [finding("sale_value"), finding("volume", share="130"), finding("price", share="90"),
         finding("sale_value", share="-50")],
        "revenue",
    )  # fmt: skip
    assert [(c.action.code, c.target, c.finding.kind) for c in found] == [
        ("bundle", None, "sale_value")
    ]


def test_different_targets_are_different_candidates():
    library = [action("promote", kinds=(("contributor", "Product"),))]
    found = rules.generate(
        library,
        [finding("contributor", "parts", "Product: A", share="60"),
         finding("contributor", "parts", "Product: B", share="40")],
        "revenue",
    )  # fmt: skip
    assert [(c.target, c.gap) for c in found] == [("A", 390), ("B", 390)]


@pytest.mark.parametrize("label", sorted(rules.NOT_A_TARGET))
def test_the_catch_all_rows_are_not_something_an_action_can_be_aimed_at(label):
    library = [action("promote", kinds=(("contributor", None),))]
    assert (
        rules.generate(
            library, [finding("contributor", "parts", f"Product: {label}", share="90")], "revenue"
        )
        == []
    )


# --- the seven scores -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("impact", "change", "score"),
    [("50", "100", 100), ("25", "100", 50), ("10", "100", 20), ("0", "100", 0), ("80", "100", 100),
     ("-25", "-100", 50), ("5", "0", 0), ("1", "300", 1)],
)  # fmt: skip
def test_winning_back_half_the_change_earns_full_marks_for_impact(impact, change, score):
    assert rules.impact_score(D(impact), D(change)) == score


def test_confidence_is_half_the_diagnosis_and_half_the_size_of_the_cause():
    assert rules.confidence_score(80, finding(share="60")) == 70  # 40 + 30
    assert rules.confidence_score(80, finding(share="500")) == 90  # the cause counts to 100 at most
    assert (
        rules.confidence_score(None, finding(share="60")) == 50
    )  # 20 + 30: a missing diagnosis counts as 40
    assert (
        rules.confidence_score(0, finding(share="-60")) == 30
    )  # a cause against the tide is judged by its size
    assert rules.confidence_score(95, finding(share=None)) == 48  # 47.5 rounds up


def goal(title="Grow", kpi="revenue", kind="increase_revenue", priority=3):
    return rules.Goal(kpi, kind, title, priority)


def test_with_no_goals_fit_is_neutral():
    assert rules.goal_fit([], "revenue", action()) == (50, None)


def test_a_goal_for_the_same_figure_fits_perfectly():
    assert rules.goal_fit([goal(kpi="revenue")], "revenue", action(kpis=("gross_profit",))) == (
        100,
        "Grow",
    )


def test_a_goal_for_a_figure_the_action_helps_also_fits_perfectly():
    assert (
        rules.goal_fit(
            [goal(kpi="gross_profit", kind="other")],
            "revenue",
            action(kpis=("revenue", "gross_profit")),
        )[0]
        == 100
    )


def test_a_goal_of_the_same_kind_fits_well():
    assert rules.goal_fit([goal(kpi=None, kind="increase_revenue")], "revenue", action()) == (
        70,
        "Grow",
    )
    assert rules.goal_fit(
        [goal(kpi="net_margin_pct", kind="increase_revenue")], "revenue", action()
    ) == (70, "Grow")


def test_goals_that_have_nothing_to_do_with_it_count_for_little():
    assert rules.goal_fit(
        [goal(kpi="net_margin_pct", kind="improve_margin")], "revenue", action()
    ) == (20, None)


def test_the_best_matching_goal_wins_and_a_tie_goes_to_the_higher_priority():
    goals = [goal("Low", kpi="revenue", priority=5), goal("Top", kpi="revenue", priority=1),
             goal("Related", kpi=None, kind="increase_revenue", priority=1)]  # fmt: skip
    assert rules.goal_fit(goals, "revenue", action()) == (100, "Top")


@pytest.mark.parametrize(
    ("severity", "days", "score"),
    [("major", 30, 90), ("notable", 30, 60), ("major", 14, 100), ("notable", 14, 70),
     ("major", 61, 80),
     ("notable", 60, 60), ("notable", 61, 50), ("odd", 30, 50), ("major", 0, 100)],
)  # fmt: skip
def test_urgency_follows_how_big_the_change_was_and_how_fast_the_action_works(
    severity, days, score
):
    assert rules.urgency_score(severity, days) == score


@pytest.mark.parametrize(
    ("effort", "cost", "score"),
    [("low", "none", 90), ("low", "low", 80), ("medium", "low", 60), ("high", "high", 0),
     ("high", "medium", 20), ("medium", "none", 70)],
)  # fmt: skip
def test_ease_is_what_is_left_after_effort_and_cost(effort, cost, score):
    assert rules.ease_score(effort, cost) == score


def test_the_total_is_the_weighted_average_of_the_six_scores():
    scores = {
        "impact": 80,
        "confidence": 60,
        "goal_fit": 100,
        "ease": 90,
        "urgency": 70,
        "history": 50,
    }
    assert rules.total_score(scores) == 77  # 7,650 / 100 = 76.5, rounded up
    assert rules.total_score({k: 100 for k in rules.WEIGHTS}) == 100
    assert rules.total_score({k: 0 for k in rules.WEIGHTS}) == 0


def evaluate(act=None, find=None, **kw):
    candidate = rules.Candidate(act or action(), find or finding(), None, D("390"))
    args = dict(
        kpi_code="revenue",
        event_change=D("-190"),
        severity="major",
        diagnosis_confidence=80,
        goals=[],
    )
    return rules.evaluate(candidate, **{**args, **kw})


def test_one_action_is_scored_on_everything_with_the_working_kept():
    item = evaluate()  # recovers 20% of 390 = 78 of a 190 change: 41% -> 82 marks
    assert item.impact_value == D("78.0")
    assert item.scores == {
        "impact": 82,
        "confidence": 90,
        "goal_fit": 50,
        "ease": 90,
        "urgency": 100,
        "history": 50,
    }
    assert item.total == 79  # (2460 + 1800 + 750 + 1350 + 1000 + 500) / 100 = 78.6, rounded up
    assert [line["key"] for line in item.breakdown] == [
        "impact",
        "confidence",
        "goal_fit",
        "ease",
        "urgency",
        "history",
    ]
    assert sum(line["points"] for line in item.breakdown) == pytest.approx(
        sum(item.scores[k] * w for k, w in rules.WEIGHTS.items()) / 100
    )


def test_a_track_record_is_neutral_until_outcomes_are_recorded():
    assert evaluate().scores["history"] == rules.NEUTRAL_HISTORY == 50


# --- ranking and wording ----------------------------------------------------------------------


def scored(code, total, impact="10", ease=50):
    item = evaluate(action(code))
    item.total, item.impact_value = total, D(impact)
    item.scores = {**item.scores, "ease": ease}
    return item


def test_the_best_total_comes_first():
    ordered = rules.rank([scored("b", 60), scored("a", 70), scored("c", 65)])
    assert [(i.rank, i.candidate.action.code) for i in ordered] == [(1, "a"), (2, "c"), (3, "b")]


def test_a_tie_goes_to_the_bigger_impact_then_the_easier_action_then_the_name():
    assert [
        i.candidate.action.code for i in rules.rank([scored("a", 70, "5"), scored("b", 70, "9")])
    ] == ["b", "a"]
    assert [
        i.candidate.action.code
        for i in rules.rank([scored("a", 70, "9", 40), scored("b", 70, "9", 80)])
    ] == ["b", "a"]
    assert [i.candidate.action.code for i in rules.rank([scored("b", 70), scored("a", 70)])] == [
        "a",
        "b",
    ]


def test_the_order_never_depends_on_the_order_they_were_found_in():
    items = [scored(c, t) for c, t in (("a", 50), ("b", 70), ("c", 70), ("d", 60))]
    forwards = [i.candidate.action.code for i in rules.rank(list(items))]
    backwards = [i.candidate.action.code for i in rules.rank(list(reversed(items)))]
    assert forwards == backwards == ["b", "c", "d", "a"]


def test_an_option_is_titled_with_what_it_is_aimed_at():
    plain = rules.Candidate(action("promote"), finding(), None, D("1"))
    aimed = rules.Candidate(action("promote"), finding(), "Sourdough", D("1"))
    assert rules.title_for(plain) == "Promote"
    assert rules.title_for(aimed) == "Promote: Sourdough"


def test_the_description_says_why_and_how_much_with_the_honest_caveat():
    candidate = rules.Candidate(
        action("promote"), finding(text="Sourdough fell."), "Sourdough", D("270")
    )
    text = rules.description_for(candidate, D("81"), "gbp")
    assert text == (
        "promote summary. Why this: Sourdough fell. We estimate it could win back about £81.00 "
        "(a starting estimate, not a promise)."
    )
    assert "estimate" not in rules.description_for(candidate, D("0"), "gbp")


def test_why_this_one_names_the_action_the_cause_the_effort_and_why_it_beat_the_next():
    best = evaluate(
        action("promote", effort="low", cost="low", days=14),
        finding("sale_value", text="The average sale shrank."),
    )
    other = evaluate(
        action("other", effort="high", cost="high", days=30), finding("sale_value", text="X.")
    )
    other.total = 40
    text = rules.rationale([best, other], "gbp")
    assert text.startswith('We recommend "Promote" (')
    assert "It is aimed at: The average sale shrank." in text
    assert "It takes low effort and low cost, and should start to show in about 14 days." in text
    assert (
        'Next best is "Other" (40 out of 100); the recommended action pulls ahead mainly on '
        in text
    )


def test_a_goal_it_fits_is_named_in_the_reason():
    best = evaluate(goals=[goal("Grow sales by 15%", kpi="revenue")])
    assert "It fits your goal: Grow sales by 15%." in rules.rationale([best], "gbp")
    assert "It fits your goal" not in rules.rationale([evaluate()], "gbp")


def test_effort_and_cost_are_said_in_plain_words():
    for effort, cost, words in (
        ("medium", "none", "moderate effort and no cost"),
        ("high", "medium", "a lot of effort and moderate cost"),
    ):
        item = evaluate(action("x", effort=effort, cost=cost))
        assert f"It takes {words}," in rules.rationale([item], "gbp")


# --- whole recommendations --------------------------------------------------------------------


def event_for(api, business, code, kind="material_change", month="2026-03-01", who="owner", org=0):
    res = api.get(
        f"{ORGS}/{business[org]}/changes?month={month}&kind={kind}&limit=500",
        headers=business[2][who],
    )
    return next(e for e in res.json() if e["kpi_code"] == code)


def recommend(api, business, event, who="owner", org=0):
    return api.post(
        f"{ORGS}/{business[org]}/changes/{event['id']}/recommendation", headers=business[2][who]
    )


def test_a_fall_in_sales_is_answered_with_ranked_options_and_one_recommendation(api, march):
    res = recommend(api, march, event_for(api, march, "revenue"))
    assert res.status_code == 200, res.text
    body = res.json()
    options = body["options"]
    assert body["status"] == "open" and body["rules_version"] == "recommend-1"
    assert len(options) == rules.MAX_OPTIONS
    assert [o["rank"] for o in options] == [1, 2, 3, 4, 5]
    assert [o["is_recommended"] for o in options] == [True, False, False, False, False]
    totals = [o["total_score"] for o in options]
    assert totals == sorted(totals, reverse=True)
    assert body["headline"] == f"Best next step for Sales in March 2026: {options[0]['title']}."
    assert body["rationale"].startswith(
        f'We recommend "{options[0]["title"]}" ({totals[0]} out of 100)'
    )


def test_every_option_is_scored_on_the_same_seven_things_with_the_working_shown(api, march):
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    for option in body["options"]:
        assert [line["key"] for line in option["scores"]] == [
            "impact", "confidence", "goal_fit", "ease", "urgency", "history",
        ]  # fmt: skip
        assert all(0 <= line["score"] <= 100 for line in option["scores"])
        assert round(sum(line["points"] for line in option["scores"])) == option["total_score"]
        assert option["effort"] in ("low", "medium", "high") and option["days_to_effect"] > 0
        assert option["description"].startswith(option["intervention"]["summary"])


def test_the_options_are_aimed_at_the_causes_the_diagnosis_found(api, march):
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    by_code = {o["intervention"]["code"]: o for o in body["options"]}
    targets = {o["intervention"]["code"]: o["target"] for o in body["options"]}
    assert targets.get("promote_product", "Sourdough") == "Sourdough"
    assert targets.get("channel_push", "Shop") == "Shop"
    assert "bundle_offer" in by_code or "win_back_regulars" in by_code
    assert all(o["target"] != "No customer recorded" for o in body["options"])


def test_the_evidence_separates_what_is_known_from_what_is_assumed_and_what_is_not(api, march):
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    kinds = [e["evidence_type"] for e in body["evidence"]]
    assert kinds[0] == "fact" and "statistical" in kinds and kinds.count("insufficient") == 2
    assumed = [e for e in body["evidence"] if "starting estimate" in e["statement"]]
    assert len(assumed) == 1 and assumed[0]["evidence_type"] == "statistical"
    reasons = [
        e["data"]["reason"] for e in body["evidence"] if e["evidence_type"] == "insufficient"
    ]
    assert reasons == ["no_history", "no_constraints"]
    assert "ai_interpretation" not in kinds
    assert [e["sort_order"] for e in body["evidence"]] == list(range(len(kinds)))


def test_it_explains_the_change_first_if_that_has_not_been_done(api, db, march):
    event = event_for(api, march, "revenue")
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0
    recommend(api, march, event)
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 1
        assert (
            db.scalars(select(DetectionEvent).where(DetectionEvent.id == event["id"])).one().status
            == "diagnosed"
        )


def test_asking_again_replaces_the_recommendation_and_keeps_one(api, db, march):
    event = event_for(api, march, "revenue")
    first = recommend(api, march, event).json()
    second = recommend(api, march, event).json()
    assert second["id"] != first["id"]
    assert [o["title"] for o in second["options"]] == [o["title"] for o in first["options"]]
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 1
        assert db.scalar(select(func.count()).select_from(RecommendationOption)) == len(
            second["options"]
        )
        assert db.scalar(select(func.count()).select_from(RecommendationEvidence)) == len(
            second["evidence"]
        )


def test_a_recommendation_can_be_read_back_after_it_is_made(api, march):
    event = event_for(api, march, "revenue")
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation"
    missing = api.get(url, headers=march[2]["owner"])
    assert (
        missing.status_code == 404 and missing.json()["error"]["code"] == "recommendation_not_found"
    )
    made = recommend(api, march, event).json()
    assert api.get(url, headers=march[2]["owner"]).json() == made


def test_a_goal_the_owner_has_set_raises_how_well_an_action_fits(api, march):
    event = event_for(api, march, "revenue")

    def fit(body):
        return {
            o["title"]: next(line["score"] for line in o["scores"] if line["key"] == "goal_fit")
            for o in body["options"]
        }

    without = fit(recommend(api, march, event).json())
    assert set(without.values()) == {50}  # no goals set: neutral
    made = api.post(
        f"{ORGS}/{march[0]}/goals",
        json={"title": "Grow sales by 15%", "goal_type": "increase_revenue", "kpi_code": "revenue"},
        headers=march[2]["owner"],
    )
    assert made.status_code == 201, made.text
    body = recommend(api, march, event).json()
    assert set(fit(body).values()) == {100}
    assert "It fits your goal: Grow sales by 15%." in body["rationale"]


def test_a_goal_that_has_been_dropped_is_not_counted(api, march):
    event = event_for(api, march, "revenue")
    made = api.post(
        f"{ORGS}/{march[0]}/goals",
        json={"title": "Old goal", "goal_type": "increase_revenue", "kpi_code": "revenue"},
        headers=march[2]["owner"],
    ).json()
    api.patch(
        f"{ORGS}/{march[0]}/goals/{made['id']}",
        json={"status": "abandoned"},
        headers=march[2]["owner"],
    )
    body = recommend(api, march, event).json()
    assert {
        next(line["score"] for line in o["scores"] if line["key"] == "goal_fit")
        for o in body["options"]
    } == {50}


def test_an_action_switched_off_in_the_library_is_not_suggested(api, db, march):
    event = event_for(api, march, "revenue")
    first = recommend(api, march, event).json()
    top = first["options"][0]["intervention"]["code"]
    db.execute(
        Intervention.__table__.update().where(Intervention.code == top).values(is_active=False)
    )
    again = recommend(api, march, event).json()
    assert top not in {o["intervention"]["code"] for o in again["options"]}


def test_good_news_needs_no_action(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 2, 1), 1000, None)
    values.put("revenue", date(2026, 3, 1), 1400, 1000)
    run(db, business)
    event = event_for(api, business, "revenue")
    assert event["effect"] == "good"
    body = recommend(api, business, event).json()
    assert body["status"] == "no_action_needed" and body["options"] == []
    assert body["headline"] == "Sales in March 2026 is good news: nothing needs fixing."
    assert "nothing to put right" in body["rationale"]
    assert [e["evidence_type"] for e in body["evidence"]] == ["fact", "statistical"]


def test_when_the_cause_cannot_be_found_nothing_is_recommended_and_it_says_so(api, march):
    event = event_for(
        api, march, "average_order_value"
    )  # the average sale fell, but it cannot be broken down
    assert event["effect"] == "bad"
    body = recommend(api, march, event).json()
    assert body["status"] == "insufficient_evidence" and body["options"] == []
    assert body["headline"] == "We can't recommend an action for Average sale in March 2026 yet."
    assert (
        body["rationale"]
        == "We could not say why this happened, so we cannot say what to do about it."
    )


def test_when_no_action_in_the_library_answers_the_cause_it_says_so(api, march, monkeypatch):
    monkeypatch.setattr(service.rules, "generate", lambda *a, **k: [])
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    assert body["status"] == "insufficient_evidence"
    assert body["rationale"] == "No action in our library answers the causes we found yet."


def test_the_library_lists_what_can_be_suggested(api, march):
    res = api.get(f"{ORGS}/{march[0]}/interventions", headers=march[2]["viewer"])
    assert res.status_code == 200
    library = res.json()
    assert len(library) == 10 and [i["name"] for i in library] == sorted(i["name"] for i in library)
    bundle = next(i for i in library if i["code"] == "bundle_offer")
    assert (
        bundle["effort"] == "low"
        and bundle["cost_level"] == "none"
        and bundle["impact_share"] == "0.2"
    )
    assert len(bundle["steps"]) == 4 and "Starting estimate" in bundle["impact_basis"]
    assert all(0 <= float(i["impact_share"]) <= 1 and i["steps"] for i in library)


def test_every_action_in_the_library_answers_something_and_helps_a_figure_we_can_explain(db):
    explainable = {"revenue", "sales_count", "units_sold", "gross_profit", "operating_expenses"}
    for item in db.scalars(select(Intervention)):
        assert item.addresses, item.code
        assert set(item.kpis) <= explainable, item.code
        assert item.effort in ("low", "medium", "high") and item.typical_days_to_effect > 0, (
            item.code
        )


def test_recommendations_are_listed_newest_first_and_can_be_filtered(api, db, march):
    revenue = recommend(api, march, event_for(api, march, "revenue")).json()
    vague = recommend(api, march, event_for(api, march, "average_order_value")).json()
    url = f"{ORGS}/{march[0]}/recommendations"
    rows = api.get(url, headers=march[2]["viewer"]).json()
    assert [r["id"] for r in rows] == [vague["id"], revenue["id"]]
    top = rows[1]
    assert top["status"] == "open" and top["recommended"] == revenue["options"][0]["title"]
    assert top["score"] == revenue["options"][0]["total_score"] and top["kpi_name"] == "Sales"
    assert rows[0]["recommended"] is None and rows[0]["score"] is None
    only = api.get(f"{url}?status=insufficient_evidence", headers=march[2]["owner"]).json()
    assert [r["id"] for r in only] == [vague["id"]]
    assert api.get(f"{url}?status=nonsense", headers=march[2]["owner"]).status_code == 422


def test_viewers_can_read_recommendations_but_not_make_them(api, march):
    event = event_for(api, march, "revenue")
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/recommendation"
    assert api.post(url, headers=march[2]["viewer"]).status_code == 403
    recommend(api, march, event)
    assert api.get(url, headers=march[2]["viewer"]).status_code == 200
    assert api.post(url).status_code == 401 and api.get(url).status_code == 401


def test_a_change_nobody_asked_about_is_not_found(api, march):
    url = f"{ORGS}/{march[0]}/changes/{uuid.uuid4()}/recommendation"
    assert api.post(url, headers=march[2]["owner"]).status_code == 404
    assert api.get(url, headers=march[2]["owner"]).status_code == 404


def test_each_business_keeps_its_recommendations_to_itself(api, db, march):
    event = event_for(api, march, "revenue")
    recommend(api, march, event)
    assert api.get(f"{ORGS}/{march[1]}/recommendations", headers=march[2]["other"]).json() == []
    other = f"{ORGS}/{march[1]}/changes/{event['id']}/recommendation"
    assert api.get(other, headers=march[2]["other"]).status_code == 404
    with scoped(db, march, 1):
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 0
        assert db.scalar(select(func.count()).select_from(RecommendationOption)) == 0


# --- the tables -------------------------------------------------------------------------------


def _event(db, business):
    with scoped(db, business):
        kpi_id = db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == "revenue")).one()
        event = DetectionEvent(
            kpi_id=kpi_id, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
            direction="down", severity="major", effect="bad", value=D("770"),
            reference_value=D("1100"), change=D("-30"), change_unit="percent",
            summary="Sales fell.", detected_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(event)
        db.flush()
        return event.id


def _recommendation(db, business, event_id, **extra):
    with scoped(db, business):
        fields = dict(
            event_id=event_id, status="open", headline="Do this.", rationale="Because.",
            rules_version="recommend-1", generated_at=datetime.now(UTC),
        )  # fmt: skip
        row = Recommendation(**{**fields, **extra})
        db.add(row)
        db.flush()
        return row.id


def _option(db, business, recommendation_id, **extra):
    with scoped(db, business):
        intervention_id = db.scalars(
            select(Intervention.id).where(Intervention.code == "bundle_offer")
        ).one()
        fields = dict(
            recommendation_id=recommendation_id, intervention_id=intervention_id, rank=1,
            is_recommended=True, title="T", description="D", impact_value=D("1"),
            impact_unit="gbp", impact_score=50, confidence=50, goal_fit=50, urgency=50, ease=50,
            history_score=50, total_score=50, effort="low", cost_level="none", days_to_effect=14,
        )  # fmt: skip
        db.add(RecommendationOption(**{**fields, **extra}))
        db.flush()


def test_a_recommendation_can_hold_ranked_options(db, business):
    rid = _recommendation(db, business, _event(db, business))
    _option(db, business, rid)
    _option(db, business, rid, rank=2, is_recommended=False)


@pytest.mark.parametrize(
    "bad",
    [{"status": "maybe"}, {"headline": "  "}],
)
def test_a_recommendation_that_makes_no_sense_is_refused(db, business, bad):
    with pytest.raises(IntegrityError):
        _recommendation(db, business, _event(db, business), **bad)


def test_there_is_one_recommendation_per_change(db, business):
    event_id = _event(db, business)
    _recommendation(db, business, event_id)
    with pytest.raises(IntegrityError):
        _recommendation(db, business, event_id)


@pytest.mark.parametrize(
    "bad",
    [{"rank": 0}, {"total_score": 101}, {"ease": -1}, {"effort": "huge"}, {"cost_level": "free"},
     {"rank": 2, "is_recommended": True}],
)  # fmt: skip
def test_an_option_that_makes_no_sense_is_refused(db, business, bad):
    rid = _recommendation(db, business, _event(db, business))
    with pytest.raises(IntegrityError):
        _option(db, business, rid, **bad)


def test_only_one_option_can_be_the_recommended_one(db, business):
    rid = _recommendation(db, business, _event(db, business))
    _option(db, business, rid)
    with pytest.raises(IntegrityError):
        _option(db, business, rid, rank=2)  # a second one claiming to be recommended


def test_two_options_cannot_share_a_rank(db, business):
    rid = _recommendation(db, business, _event(db, business))
    _option(db, business, rid)
    with pytest.raises(IntegrityError):
        _option(db, business, rid, is_recommended=False)


def test_options_cannot_be_attached_to_another_businesss_recommendation(db, business):
    rid = _recommendation(db, business, _event(db, business))
    with pytest.raises(IntegrityError):
        _option_in_other_business(db, business, rid)


def _option_in_other_business(db, business, rid):
    with scoped(db, business, 1):
        intervention_id = db.scalars(select(Intervention.id).limit(1)).one()
        db.add(
            RecommendationOption(
                recommendation_id=rid, intervention_id=intervention_id, rank=1, is_recommended=True,
                title="T", description="D", impact_value=D("1"), impact_unit="gbp",
                impact_score=1, confidence=1, goal_fit=1, urgency=1, ease=1, history_score=1,
                total_score=1, effort="low", cost_level="none", days_to_effect=1,
            )
        )  # fmt: skip
        db.flush()


def test_removing_a_change_removes_what_was_recommended_for_it(db, business):
    event_id = _event(db, business)
    rid = _recommendation(db, business, event_id)
    _option(db, business, rid)
    with scoped(db, business):
        db.add(RecommendationEvidence(recommendation_id=rid, evidence_type="fact", statement="X."))
        db.flush()
        db.execute(DetectionEvent.__table__.delete())
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 0
        assert db.scalar(select(func.count()).select_from(RecommendationOption)) == 0
        assert db.scalar(select(func.count()).select_from(RecommendationEvidence)) == 0


def test_the_library_refuses_nonsense(db):
    base = dict(
        code="x_y", name="N", category="sales", summary="S", effort="low", cost_level="none",
        typical_days_to_effect=1, impact_share=D("0.1"), impact_basis="B",
    )  # fmt: skip
    for bad in (
        {"code": "Bad Code"},
        {"category": "weather"},
        {"effort": "huge"},
        {"impact_share": D("1.5")},
        {"typical_days_to_effect": -1},
        {"name": " "},
        {"code": "bundle_offer"},
    ):
        with pytest.raises(IntegrityError):
            db.add(Intervention(**{**base, **bad}))
            db.flush()
        db.rollback()


# --- found by trying to break it ---------------------------------------------------------------


def test_only_a_part_is_read_as_a_named_thing_even_if_a_title_has_a_colon():
    assert rules.part_of(finding("sale_value", label="Size of sale: smaller")) == (None, None)


def test_the_bigger_impact_wins_a_tie_before_the_easier_action_does():
    big = scored("big", 70, "9", 40)
    easy = scored("easy", 70, "5", 80)
    assert [i.candidate.action.code for i in rules.rank([easy, big])] == ["big", "easy"]


def test_an_action_that_wins_back_nothing_does_not_claim_to():
    nothing = evaluate(action("tidy", share="0"))
    assert nothing.impact_value == 0
    assert "win back" not in rules.rationale([nothing], "gbp")
    assert "win back" not in rules.description_for(nothing.candidate, nothing.impact_value, "gbp")


def test_a_change_that_is_neither_good_nor_bad_needs_no_action(api, db, business):
    values = Values(db, business)
    values.put("cost_of_goods_sold", date(2026, 2, 1), 500, None)
    values.put("cost_of_goods_sold", date(2026, 3, 1), 700, 500)
    run(db, business)
    event = event_for(api, business, "cost_of_goods_sold")
    assert event["effect"] == "neutral"
    body = recommend(api, business, event).json()
    assert body["status"] == "no_action_needed" and body["options"] == []


def test_when_the_diagnosis_could_not_say_why_the_reason_given_is_that(api, db, march, monkeypatch):
    event = event_for(api, march, "revenue")
    recommend(api, march, event)  # makes the diagnosis
    with scoped(db, march):
        db.execute(
            Diagnosis.__table__.update().values(
                status="insufficient_evidence", confidence=None, confidence_label="insufficient"
            )
        )
    monkeypatch.setattr(service.rules, "generate", lambda *a, **k: [])
    body = recommend(api, march, event).json()
    assert body["status"] == "insufficient_evidence"
    assert (
        body["rationale"]
        == "We could not say why this happened, so we cannot say what to do about it."
    )


def test_the_money_each_option_could_win_back_is_kept_and_matches_what_is_said(api, march):
    body = recommend(api, march, event_for(api, march, "revenue")).json()
    for option in body["options"]:
        assert float(option["impact_value"]) > 0 and option["impact_unit"] in ("gbp", "count")
        assert len(option["impact_value"].split(".")[1]) == 2
        assert (
            f"about {rules.format_value(D(option['impact_value']), option['impact_unit'])}"
            in option["description"]
        )


def test_an_action_that_can_win_something_back_says_how_much_in_the_reason():
    best = evaluate(action("promote", share="0.2"))  # 20% of the 390 gap
    assert (
        "It could win back about £78.00 (a starting estimate, not a promise)."
        in rules.rationale([best], "gbp")
    )
