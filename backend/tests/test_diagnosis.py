"""Diagnosis: wording and confidence rules (pure), then whole diagnoses on shops done by hand."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.diagnostics import evidence as rules
from app.models.diagnostics import DetectionEvent, Diagnosis, DiagnosticEvidence
from app.services import detection, kpi
from tests.test_detection import Values, run, with_summer
from tests.test_health import ORGS, owner_tenant, scoped

D = Decimal


def finding(kind="sales_count", lens="orders", amount="-100", share="80", label=None, text="T."):
    return SimpleNamespace(
        kind=kind, lens=lens, amount=amount, share_pct=share, label=label or kind, text=text
    )


# --- the statements -------------------------------------------------------------------------


def test_the_facts_say_what_the_records_hold():
    [fact] = rules.figure_facts(
        "material_change", "Sales", "gbp", "March 2026", "February 2026", D(770), D(1100), 95
    )
    assert fact.evidence_type == "fact"
    assert fact.statement == "Sales was £770.00 in March 2026 and £1,100.00 in February 2026."


def test_for_an_unusual_month_the_reference_is_the_usual_level():
    [fact] = rules.figure_facts(
        "anomaly", "Sales", "gbp", "March 2026", "February 2026", D(770), D(1000), None
    )
    assert fact.statement == "Sales was £770.00 in March 2026; its usual is £1,000.00."


def test_incomplete_data_is_called_out_below_eighty_and_not_at_eighty():
    low = rules.figure_facts("material_change", "Sales", "gbp", "M", "B", D(1), D(2), 79)
    assert len(low) == 2 and low[1].evidence_type == "fact"
    assert "79 out of 100" in low[1].statement
    assert len(rules.figure_facts("material_change", "Sales", "gbp", "M", "B", D(1), D(2), 80)) == 1
    assert (
        len(rules.figure_facts("material_change", "Sales", "gbp", "M", "B", D(1), D(2), None)) == 1
    )


def test_a_part_read_off_the_records_is_a_fact_and_arithmetic_is_statistical():
    assert rules.finding_item(finding(lens="parts", kind="contributor")).evidence_type == "fact"
    for lens in ("days", "orders", "price_volume"):
        assert rules.finding_item(finding(lens=lens)).evidence_type == "statistical"
    item = rules.finding_item(finding(share="-12.5", label="Product: X", text="Said."))
    assert item.statement == "Said."
    assert item.data["share_pct"] == "-12.5" and item.data["label"] == "Product: X"


def test_how_far_from_usual_and_what_the_seasons_expect_are_statistics():
    spread = rules.history_item("-4.2", "1000.00", 12, "July 2026")
    assert spread.evidence_type == "statistical" and spread.statement == (
        "July 2026 was 4.2 times further from the middle of the last 12 months than this figure "
        "normally strays."
    )
    season = rules.season_item("50.0", "August 2026")
    assert season.statement == (
        "The busy and quiet seasons you entered lead you to expect a change of about +50% in "
        "August 2026."
    )
    assert rules.season_item("-33.3", "September 2026").statement.count("-33%") == 1


def test_what_cannot_be_told_is_said_plainly():
    outside = "cannot tell whether things outside them"
    only = rules.limits(explainable=True, findings=[finding(share="80")], no_detail_share=None)
    assert [i.data["reason"] for i in only] == ["outside_the_records"]
    assert outside in only[0].statement and only[0].evidence_type == "insufficient"

    spread = rules.limits(explainable=True, findings=[finding(share="24.9")], no_detail_share=None)
    assert [i.data["reason"] for i in spread] == ["spread_widely", "outside_the_records"]
    assert (
        rules.limits(explainable=True, findings=[], no_detail_share=None)[0].data["reason"]
        == "spread_widely"
    )

    cannot = rules.limits(explainable=False, findings=[], no_detail_share=None)
    assert [i.data["reason"] for i in cannot] == ["not_explainable", "outside_the_records"]


def test_a_cause_of_exactly_a_quarter_is_enough_to_say_something_stands_out():
    items = rules.limits(explainable=True, findings=[finding(share="25")], no_detail_share=None)
    assert [i.data["reason"] for i in items] == ["outside_the_records"]
    negative = rules.limits(explainable=True, findings=[finding(share="-25")], no_detail_share=None)
    assert [i.data["reason"] for i in negative] == ["outside_the_records"]


def test_change_hidden_in_sales_with_no_product_detail_is_called_out_from_a_quarter():
    reasons = lambda share: [  # noqa: E731
        i.data["reason"]
        for i in rules.limits(explainable=True, findings=[finding()], no_detail_share=share)
    ]
    assert "no_product_detail" not in reasons(D("24.9"))
    assert "no_product_detail" in reasons(D("25"))
    assert "no_product_detail" not in reasons(None)
    detail = next(
        i for i in rules.limits(explainable=True, findings=[finding()], no_detail_share=D("60"))
        if i.data["reason"] == "no_product_detail"
    )  # fmt: skip
    assert detail.statement.startswith(
        "60% of the change is in sales recorded without product detail"
    )


# --- the headline ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "unit", "change", "change_unit", "direction", "expected"),
    [
        ("material_change", "gbp", "-31.7", "percent", "down", "Sales fell by 32% in March 2026"),
        ("material_change", "gbp", "12", "percent", "up", "Sales rose by 12% in March 2026"),
        ("material_change", "percent", "-5.2", "points", "down",
         "Sales fell by 5.2 points in March 2026"),
        ("anomaly", "gbp", "-30", "percent", "down",
         "Sales in March 2026 was 30% below its usual"),
        ("anomaly", "gbp", "30", "percent", "up",
         "Sales in March 2026 was 30% above its usual"),
        ("anomaly", "percent", "3.5", "points", "up",
         "Sales in March 2026 was 3.5 points above its usual"),
    ],
)  # fmt: skip
def test_the_opening_of_the_headline_says_what_happened(
    kind, unit, change, change_unit, direction, expected
):
    assert (
        rules.lead_clause(kind, "Sales", unit, "March 2026", D(change), change_unit, direction)
        == expected
    )


@pytest.mark.parametrize(
    ("kind", "amount", "label", "phrase"),
    [
        ("sales_count", "5", "x", "there were more sales"),
        ("sales_count", "-5", "x", "there were fewer sales"),
        ("sale_value", "5", "x", "the average sale was bigger"),
        ("sale_value", "-5", "x", "the average sale was smaller"),
        ("volume", "5", "x", "more items were sold"),
        ("volume", "-5", "x", "fewer items were sold"),
        ("price", "5", "x", "the prices charged were higher"),
        ("price", "-5", "x", "the prices charged were lower"),
        ("calendar_days", "5", "x", "the month had more days"),
        ("calendar_days", "-5", "x", "the month had fewer days"),
        ("daily_rate", "5", "x", "an average day brought in more"),
        ("daily_rate", "-5", "x", "an average day brought in less"),
        ("no_product_detail", "-5", "x", "sales recorded without product detail changed"),
        ("contributor", "-5", "Product: Sourdough", "Sourdough fell"),
        ("contributor", "5", "Sales channel: Website", "Website rose"),
    ],
)  # fmt: skip
def test_each_kind_of_cause_reads_as_a_reason(kind, amount, label, phrase):
    assert rules.cause_phrase(finding(kind=kind, amount=amount, label=label)) == phrase


def test_the_headline_names_the_two_main_causes():
    findings = [
        finding(kind="sale_value", amount="-390", share="205.3"),
        finding(kind="contributor", amount="-270", share="142.1", label="Customer: Jo"),
        finding(kind="sales_count", amount="200", share="-105.3"),
    ]
    assert rules.headline("Sales fell by 32% in March 2026", findings, "ready") == (
        "Sales fell by 32% in March 2026, mainly because the average sale was smaller and Jo fell."
    )


def test_only_causes_of_at_least_half_the_change_count_as_main_causes():
    findings = [
        finding(kind="volume", amount="-60", share="60"),
        finding(kind="price", amount="-49", share="49.9"),
    ]
    assert rules.headline("L", findings, "ready") == "L, mainly because fewer items were sold."


def test_with_no_main_cause_the_strongest_is_named():
    findings = [
        finding(kind="price", amount="-30", share="30"),
        finding(kind="volume", amount="-26", share="26"),
    ]
    assert (
        rules.headline("L", findings, "ready") == "L, mainly because the prices charged were lower."
    )


def test_when_there_is_not_enough_evidence_the_headline_says_so():
    assert rules.headline("Sales fell by 32% in March 2026", [], "insufficient_evidence") == (
        "Sales fell by 32% in March 2026, but there is not enough evidence to say why."
    )


# --- how sure we are ------------------------------------------------------------------------


def sure(share, quality=100, extra=(), explainable=True, **kw):
    findings = [finding(share=share, **kw), *extra]
    return rules.confidence(findings, quality, explainable)


def test_a_cause_that_explains_it_all_from_complete_data_is_high_but_never_certain():
    result = sure("100", 100)  # 60 + 40 = 100, held at 95
    assert (result.score, result.label, result.status) == (95, "high", "ready")


def test_confidence_is_sixty_per_cent_the_cause_and_forty_per_cent_the_data():
    assert sure("60", 80).score == 68  # 36 + 32
    assert sure("30", 100).score == 58  # 18 + 40
    assert sure("30", 20).score == 26  # 18 + 8
    assert sure("100", 0).score == 60  # all explained, but no data to trust


def test_how_much_of_the_change_is_explained_counts_up_to_all_of_it():
    assert sure("500", 100).score == sure("100", 100).score
    assert sure("500", 20).score == sure("100", 20).score == 68  # 60 + 8, not 300 + 8
    assert sure("-80", 100).score == sure("80", 100).score  # a cause against the tide counts too


def test_the_bands_are_low_medium_and_high():
    assert (sure("100", 51).score, sure("100", 51).label) == (80, "high")  # 60 + 20.4
    assert sure("100", 40).score == 76 and sure("100", 40).label == "high"
    assert sure("100", 35).score == 74 and sure("100", 35).label == "medium"
    assert sure("30", 80).label == "medium" and sure("30", 80).score == 50  # 18 + 32
    assert sure("30", 77).score == 49 and sure("30", 77).label == "low"  # 18 + 30.8


def test_several_independent_readings_pointing_the_same_way_add_a_little():
    base = sure("60", 80)  # 36 + 32 = 68
    days = finding(lens="days", share="55")
    parts = finding(lens="parts", share="52")
    prices = finding(lens="price_volume", share="51")
    one = sure("60", 80, extra=[days])
    two = sure("60", 80, extra=[days, parts])
    three = sure("60", 80, extra=[days, parts, prices])
    assert (base.score, one.score, two.score, three.score) == (68, 78, 88, 88)  # capped at two


def test_a_second_reading_on_the_same_lens_or_a_weak_one_adds_nothing():
    same = sure("60", 80, extra=[finding(lens="orders", share="55")])
    weak = sure("60", 80, extra=[finding(lens="days", share="49")])
    assert same.score == 68 and weak.score == 68


def test_change_hidden_in_sales_with_no_product_detail_lowers_confidence():
    hidden = sure(
        "60", 80, extra=[finding(kind="no_product_detail", lens="price_volume", share="25")]
    )
    assert hidden.score == 53  # 36 + 32 - 15
    small = sure(
        "60", 80, extra=[finding(kind="no_product_detail", lens="price_volume", share="24")]
    )
    assert small.score == 68


def test_missing_data_quality_is_taken_as_complete():
    assert sure("60", None).score == sure("60", 100).score == 76


def test_confidence_cannot_go_below_nothing():
    result = rules.confidence(
        [finding(kind="no_product_detail", lens="price_volume", share="25")], 0, True
    )
    assert result.score == 0 and result.label == "low"


def test_with_no_cause_that_stands_out_there_is_no_confidence_to_give():
    for result in (
        rules.confidence([], 100, True),
        rules.confidence([finding(share="24.9")], 100, True),
        rules.confidence([finding(share="90")], 100, False),
    ):
        assert (result.score, result.label, result.status) == (
            None,
            "insufficient",
            "insufficient_evidence",
        )


def test_a_cause_of_exactly_a_quarter_is_enough_to_have_a_confidence():
    assert sure("25", 100).score == 55  # 15 + 40


def test_the_confidence_says_how_it_was_reached():
    result = sure("100", 70, extra=[finding(lens="days", share="80")])
    assert result.note == (
        "This is high confidence because the strongest cause accounts for 100% of the change; "
        "the data is 70 out of 100 complete; 2 different readings point the same way."
    )
    hidden = sure(
        "60", 100, extra=[finding(kind="no_product_detail", lens="price_volume", share="30")]
    )
    assert hidden.note.endswith("some of the change sits in sales with no product detail.")


# --- whole diagnoses on a shop worked out by hand --------------------------------------------


def event_for(api, business, code, kind="material_change", month="2026-03-01", who="owner", org=0):
    res = api.get(
        f"{ORGS}/{business[org]}/changes?month={month}&kind={kind}&limit=500",
        headers=business[2][who],
    )
    return next(e for e in res.json() if e["kpi_code"] == code)


def diagnose_it(api, business, event, who="owner", org=0):
    return api.post(
        f"{ORGS}/{business[org]}/changes/{event['id']}/diagnosis", headers=business[2][who]
    )


def test_a_fall_in_sales_is_explained_with_typed_evidence(api, march):
    event = event_for(api, march, "revenue")
    res = diagnose_it(api, march, event)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ready" and body["rules_version"] == "diagnosis-1"
    assert body["headline"] == (
        "Sales fell by 32% in March 2026, mainly because the average sale was smaller and Jo fell."
    )
    assert body["event"]["id"] == event["id"]
    kinds = [e["evidence_type"] for e in body["evidence"]]
    assert kinds[0] == "fact" and kinds[-1] == "insufficient"
    assert body["evidence"][0]["statement"] == (
        "Sales was £410.00 in March 2026 and £600.00 in February 2026."
    )
    assert {"fact", "statistical", "insufficient"} <= set(kinds)
    assert [e["sort_order"] for e in body["evidence"]] == list(range(len(kinds)))


def test_each_kind_of_statement_is_kept_apart(api, march):
    body = diagnose_it(api, march, event_for(api, march, "revenue")).json()
    by_type = {}
    for item in body["evidence"]:
        by_type.setdefault(item["evidence_type"], []).append(item)
    assert any(
        "Sourdough" in e["statement"] for e in by_type["fact"]
    )  # a part read off the records
    assert any(e["data"].get("lens") == "orders" for e in by_type["statistical"])  # worked out
    assert all(e["data"].get("lens") != "parts" for e in by_type["statistical"])
    assert "ai_interpretation" not in by_type  # nothing is ever passed off as the assistant's
    assert len(by_type["insufficient"]) == 1  # it cannot see outside the records


def test_the_confidence_follows_the_rules_and_says_how(api, march):
    event = event_for(api, march, "revenue")
    body = diagnose_it(api, march, event).json()
    quality = event["data_quality"]
    expected = min(
        95, int((D("0.6") * 100 + D("0.4") * quality + 20).quantize(D("1"), "ROUND_HALF_UP"))
    )
    assert body["confidence"] == expected
    assert body["confidence_label"] == ("high" if expected >= 75 else "medium")
    assert body["confidence_note"].startswith(
        f"This is {body['confidence_label']} confidence because"
    )
    assert body["summary"].startswith(body["headline"]) and body["summary"].endswith(
        body["confidence_note"]
    )


def test_the_diagnosis_records_the_rules_and_the_figures_it_was_built_from(api, db, march):
    body = diagnose_it(api, march, event_for(api, march, "revenue")).json()
    with scoped(db, march):
        stored = db.scalars(select(Diagnosis)).one()
        assert stored.rules_version == "diagnosis-1"
        drivers = stored.basis["drivers"]
        assert drivers["metric"] == "revenue" and drivers["total_change"] == "-190.00"
        assert [lens["key"] for lens in drivers["lenses"]] == ["days", "orders", "price_volume"]
        assert str(stored.id) == body["id"]


def test_explaining_a_change_marks_it_as_diagnosed(api, march):
    event = event_for(api, march, "revenue")
    assert event["status"] == "open"
    diagnose_it(api, march, event)
    assert event_for(api, march, "revenue")["status"] == "diagnosed"


def test_a_diagnosis_can_be_read_back_after_it_is_made(api, march):
    event = event_for(api, march, "revenue")
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/diagnosis"
    missing = api.get(url, headers=march[2]["owner"])
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "diagnosis_not_found"
    made = diagnose_it(api, march, event).json()
    assert api.get(url, headers=march[2]["owner"]).json() == made


def test_explaining_again_replaces_the_evidence_and_keeps_one_diagnosis(api, db, march):
    event = event_for(api, march, "revenue")
    first = diagnose_it(api, march, event).json()
    second = diagnose_it(api, march, event).json()
    assert second["id"] == first["id"]  # the same diagnosis, brought up to date
    assert len(second["evidence"]) == len(first["evidence"])
    assert {e["id"] for e in first["evidence"]}.isdisjoint({e["id"] for e in second["evidence"]})
    assert second["diagnosed_at"] >= first["diagnosed_at"]
    with scoped(db, march):
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 1
        assert db.scalar(select(func.count()).select_from(DiagnosticEvidence)) == len(
            second["evidence"]
        )


def test_finding_the_changes_again_keeps_the_diagnosis_and_its_status(api, db, march):
    event = event_for(api, march, "revenue")
    diagnose_it(api, march, event)
    run(db, march)
    again = event_for(api, march, "revenue")
    assert again["id"] == event["id"] and again["status"] == "diagnosed"
    assert (
        api.get(
            f"{ORGS}/{march[0]}/changes/{event['id']}/diagnosis", headers=march[2]["owner"]
        ).status_code
        == 200
    )


def test_a_figure_the_engine_cannot_break_down_yet_is_not_guessed_at(api, march):
    event = event_for(api, march, "average_order_value")
    body = diagnose_it(api, march, event).json()
    assert body["status"] == "insufficient_evidence" and body["confidence"] is None
    assert body["confidence_label"] == "insufficient"
    assert body["headline"].endswith("but there is not enough evidence to say why.")
    reasons = [
        e["data"].get("reason") for e in body["evidence"] if e["evidence_type"] == "insufficient"
    ]
    assert reasons == ["not_explainable", "outside_the_records"]
    assert [e["evidence_type"] for e in body["evidence"]][0] == "fact"


def test_running_costs_are_explained_by_their_parts(api, db, business, costs):
    with scoped(db, business):
        tenant = owner_tenant(db, business)
        kpi.calculate(
            db, tenant, granularity="month", first=date(2026, 2, 1), last=date(2026, 3, 1)
        )
        detection.detect(db, tenant)
    body = diagnose_it(api, business, event_for(api, business, "operating_expenses")).json()
    assert body["status"] == "ready"
    assert body["headline"] == (
        "Running costs rose by 22% in March 2026, mainly because No supplier rose and "
        "Utilities rose."
    )
    assert [e["evidence_type"] for e in body["evidence"]].count("fact") >= 3


def test_an_unusual_month_gets_its_distance_from_usual_as_evidence(api, db, business):
    values = Values(db, business)
    values.series("revenue", [1000, 1050, 950, 1000, 1020, 980, 700])
    run(db, business)
    event = event_for(api, business, "revenue", kind="anomaly", month="2026-07-01")
    body = diagnose_it(api, business, event).json()
    statements = [e["statement"] for e in body["evidence"]]
    assert statements[0] == "Sales was £700.00 in July 2026; its usual is £1,000.00."
    stat = next(e for e in body["evidence"] if e["evidence_type"] == "statistical")
    assert "times further from the middle of the last 6 months" in stat["statement"]
    assert body["headline"].startswith("Sales in July 2026 was 30% below its usual")


def test_an_unusual_month_that_did_not_move_on_the_month_before_cannot_be_broken_down(
    api, db, business
):
    values = Values(db, business)
    values.series(
        "revenue", [1000] * 6 + [940, 880, 800]
    )  # a slow slide: only September is unusual
    run(db, business)
    event = event_for(api, business, "revenue", kind="anomaly", month="2026-09-01")
    body = diagnose_it(api, business, event).json()
    assert body["status"] == "insufficient_evidence" and body["confidence"] is None
    reasons = [
        e["data"].get("reason") for e in body["evidence"] if e["evidence_type"] == "insufficient"
    ]
    assert "not_explainable" in reasons


def test_a_change_the_seasons_expect_says_so_in_the_evidence(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1500, 1000)
    with_summer(db, business)
    run(db, business)
    event = event_for(api, business, "revenue", month="2026-08-01")
    assert event["explained_by_season"]
    body = diagnose_it(api, business, event).json()
    season = [e for e in body["evidence"] if "busy and quiet seasons" in e["statement"]]
    assert len(season) == 1 and season[0]["evidence_type"] == "statistical"
    assert "+50%" in season[0]["statement"]


def test_incomplete_data_is_called_out_and_lowers_the_confidence(api, db, bakery):
    with scoped(db, bakery):
        tenant = owner_tenant(db, bakery)
        kpi.calculate(
            db, tenant, granularity="month", first=date(2026, 2, 1), last=date(2026, 3, 1)
        )
        db.execute(
            DetectionEvent.__table__.update().values(data_quality=55)
        )  # pretend the month's data is patchy
        detection.detect(db, tenant)
    event = event_for(api, bakery, "revenue")
    with scoped(db, bakery):
        db.execute(
            DetectionEvent.__table__.update()
            .where(DetectionEvent.id == event["id"])
            .values(data_quality=55)
        )
    body = diagnose_it(api, bakery, event).json()
    assert any(
        e["evidence_type"] == "fact" and "incomplete (a data-quality score of 55" in e["statement"]
        for e in body["evidence"]
    )
    assert body["confidence"] == min(95, round(60 + 0.4 * 55 + 20))


def test_a_change_nobody_asked_about_is_not_found(api, march):
    import uuid

    url = f"{ORGS}/{march[0]}/changes/{uuid.uuid4()}/diagnosis"
    assert api.post(url, headers=march[2]["owner"]).status_code == 404
    assert api.get(url, headers=march[2]["owner"]).status_code == 404


def test_viewers_can_read_a_diagnosis_but_not_make_one(api, march):
    event = event_for(api, march, "revenue")
    url = f"{ORGS}/{march[0]}/changes/{event['id']}/diagnosis"
    assert api.post(url, headers=march[2]["viewer"]).status_code == 403
    assert api.get(url, headers=march[2]["viewer"]).status_code == 404  # nothing made yet
    diagnose_it(api, march, event)
    assert api.get(url, headers=march[2]["viewer"]).status_code == 200
    assert api.post(url).status_code == 401 and api.get(url).status_code == 401


def test_each_business_keeps_its_diagnoses_to_itself(api, db, march):
    event = event_for(api, march, "revenue")
    diagnose_it(api, march, event)
    url = f"{ORGS}/{march[1]}/changes/{event['id']}/diagnosis"
    assert (
        api.get(url, headers=march[2]["other"]).status_code == 404
    )  # the rival has no such change
    with scoped(db, march, 1):
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0
        assert db.scalar(select(func.count()).select_from(DiagnosticEvidence)) == 0


def test_a_diagnosis_goes_when_its_change_does(api, db, march):
    event = event_for(api, march, "revenue")
    diagnose_it(api, march, event)
    with scoped(db, march):
        db.execute(DetectionEvent.__table__.delete().where(DetectionEvent.id == event["id"]))
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0
        assert db.scalar(select(func.count()).select_from(DiagnosticEvidence)) == 0
    _ = (datetime.now(UTC), D)


def test_the_summary_gives_the_headline_the_main_causes_and_how_sure_we_are(api, march):
    body = diagnose_it(api, march, event_for(api, march, "revenue")).json()
    causes = [
        e["statement"]
        for e in body["evidence"]
        if e["data"].get("share_pct") is not None and abs(D(e["data"]["share_pct"])) >= 25
    ]
    assert len(causes) >= 4
    expected = " ".join([body["headline"], *causes[:3], body["confidence_note"]])
    assert body["summary"] == expected  # three causes at most, strongest first


def test_a_change_the_seasons_do_not_account_for_gets_no_season_evidence(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1800, 1000)  # +80%: the season only explains 50
    with_summer(db, business)
    run(db, business)
    event = event_for(api, business, "revenue", month="2026-08-01")
    assert not event["explained_by_season"]
    body = diagnose_it(api, business, event).json()
    assert not [e for e in body["evidence"] if "busy and quiet seasons" in e["statement"]]


def test_a_cause_hidden_in_sales_with_no_product_detail_is_called_out_and_costs_confidence(
    api, db, business
):
    from app.models.data import Product
    from tests.shops import _make, _sale

    cake = _make(db, business, Product, name="Cake")
    _sale(db, business, date(2026, 2, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 2, 4), 100)  # no lines
    _sale(db, business, date(2026, 3, 3), 100, line=(cake, 10, 40))
    _sale(db, business, date(2026, 3, 4), 30)  # no lines
    with scoped(db, business):
        tenant = owner_tenant(db, business)
        kpi.calculate(
            db, tenant, granularity="month", first=date(2026, 2, 1), last=date(2026, 3, 1)
        )
        detection.detect(db, tenant)
    body = diagnose_it(api, business, event_for(api, business, "revenue")).json()
    reasons = [
        e["data"].get("reason") for e in body["evidence"] if e["evidence_type"] == "insufficient"
    ]
    assert reasons == ["no_product_detail", "outside_the_records"]
    assert body["confidence_note"].endswith(
        "some of the change sits in sales with no product detail."
    )


def test_the_summary_leaves_out_weak_causes_and_stops_at_three():
    strong = [finding(share=str(90 - i), text=f"Cause {i}.") for i in range(5)]
    weak = [finding(share="24.9", text="Weak."), finding(share=None, text="Nothing.")]
    assert (
        rules.summary_of("Head.", [*strong[:2], *weak], "Sure.") == "Head. Cause 0. Cause 1. Sure."
    )
    assert rules.summary_of("Head.", strong, "Sure.") == "Head. Cause 0. Cause 1. Cause 2. Sure."
    assert rules.summary_of("Head.", weak, "Sure.") == "Head. Sure."
