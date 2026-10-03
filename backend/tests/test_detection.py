"""Detection of material changes: the rules, then the engine on KPI values worked out by hand."""

import uuid
from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.diagnostics import detection as rules
from app.models.diagnostics import DetectionEvent, Diagnosis, DiagnosticEvidence
from app.models.health import HealthRule
from app.models.kpi import KpiCalculationRun, KpiDefinition, KpiValue
from app.services import detection
from tests.test_health import ORGS, owner_tenant, scoped, season

D = Decimal


# --- is a change big enough? ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "value", "previous", "expected"),
    [
        ("gbp", "770", "1100", (D("-30"), "percent")),
        ("gbp", "1210", "1100", (D("10"), "percent")),
        ("count", "30", "20", (D("50"), "percent")),
        ("gbp", "1000", "-500", (D("300"), "percent")),  # measured against the size of the start
        ("percent", "7", "12", (D("-5"), "points")),
        ("percent", "15", "7", (D("8"), "points")),
        ("percent", "5", "0", (D("5"), "points")),  # a margin can start from nothing
    ],
)
def test_how_far_a_figure_moved(unit, value, previous, expected):
    assert rules.measure_change(unit, D(value), D(previous)) == expected


@pytest.mark.parametrize(
    ("unit", "previous"),
    [("gbp", "99.99"), ("gbp", "0"), ("count", "4"), ("ratio", "0.05")],
)
def test_a_tiny_starting_point_says_nothing(unit, previous):
    assert rules.measure_change(unit, D("1000"), D(previous)) is None


def test_a_starting_point_exactly_at_the_minimum_counts():
    assert rules.measure_change("gbp", D("150"), D("100")) == (D("50"), "percent")
    assert rules.measure_change("count", D("10"), D("5")) == (D("100"), "percent")


@pytest.mark.parametrize(
    ("change", "unit", "severity"),
    [
        ("14.99", "percent", None), ("15", "percent", "notable"), ("29.99", "percent", "notable"),
        ("30", "percent", "major"), ("-30", "percent", "major"), ("-15", "percent", "notable"),
        ("2.99", "points", None), ("3", "points", "notable"), ("7.99", "points", "notable"),
        ("8", "points", "major"), ("-8", "points", "major"), ("-3", "points", "notable"),
    ],
)  # fmt: skip
def test_the_size_bands(change, unit, severity):
    assert rules.severity_for(D(change), unit) == severity


@pytest.mark.parametrize(
    ("direction", "change", "effect"),
    [
        ("up_good", "10", "good"), ("up_good", "-10", "bad"),
        ("down_good", "10", "bad"), ("down_good", "-10", "good"),
        ("neutral", "10", "neutral"), ("neutral", "-10", "neutral"),
        ("up_good", "0", "neutral"),
    ],
)  # fmt: skip
def test_whether_a_change_is_good_or_bad_news_depends_on_which_way_is_better(
    direction, change, effect
):
    assert rules.effect_for(direction, D(change)) == effect


def test_the_change_the_seasons_lead_you_to_expect():
    # 50% busier than normal after a normal month: expect sales to rise 50%
    assert rules.expected_season_change(D("50"), D("0")) == 50
    # ...and back to normal after it: 1 / 1.5 - 1
    assert rules.expected_season_change(D("0"), D("50")).quantize(D("0.01")) == D("-33.33")
    assert rules.expected_season_change(D("0"), D("0")) == 0
    assert rules.expected_season_change(D("-100"), D("0")) is None
    assert rules.expected_season_change(D("0"), D("-100")) is None


@pytest.mark.parametrize(
    ("change", "expected", "explained"),
    [
        ("45", "50", True), ("75", "50", True), ("76", "50", False),  # 50 is two thirds of 75
        ("-30", "-33.3", True), ("45", "-50", False),  # the wrong way round
        ("45", "0", False), ("45", None, False), ("0", "50", False),
    ],
)  # fmt: skip
def test_the_seasons_explain_a_change_when_they_account_for_most_of_it(change, expected, explained):
    assert rules.season_explains(D(change), None if expected is None else D(expected)) is explained


def test_the_sentences_say_what_moved_and_by_how_much():
    assert (
        rules.describe(
            "Sales", "gbp", "March 2026", "February 2026", D("770"), D("1100"), D("-30"), "percent"
        )
        == "Sales fell by 30% in March 2026: £770.00, down from £1,100.00 in February 2026."
    )
    assert (
        rules.describe(
            "Profit margin",
            "percent",
            "April 2026",
            "March 2026",
            D("15"),
            D("7"),
            D("8"),
            "points",
        )
        == "Profit margin rose by 8.0 points in April 2026: 15.0%, up from 7.0% in March 2026."
    )
    assert rules.describe(
        "Sales", "gbp", "August 2026", "July 2026", D("1500"), D("1000"), D("50"), "percent",
        explained_by_season=True,
    ).endswith("would lead you to expect.")  # fmt: skip


# --- the engine, on KPI values worked out by hand -------------------------------------------


class Values:
    """Writes KPI values, with the previous month's, straight into the table as the engine would."""

    def __init__(self, db, business, org=0):
        self.db, self.business, self.org = db, business, org
        with scoped(db, business, org):
            run = KpiCalculationRun(
                granularity="month", period_from=date(2026, 1, 1), period_to=date(2026, 12, 31),
                started_at=datetime.now(UTC), finished_at=datetime.now(UTC), status="succeeded",
            )  # fmt: skip
            db.add(run)
            db.flush()
            self.run_id = run.id

    def put(self, code, month, value, previous, *, status="ok", complete=True, quality=100):
        kpi_id = self.db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == code)).one()
        with scoped(self.db, self.business, self.org):
            self.db.add(
                KpiValue(
                    kpi_id=kpi_id, run_id=self.run_id, granularity="month", period_start=month,
                    period_end=month.replace(day=monthrange(month.year, month.month)[1]),
                    is_complete=complete, status=status,
                    value=None if value is None else D(str(value)),
                    previous_value=None if previous is None else D(str(previous)),
                    data_quality=quality, inputs={}, calculated_at=datetime.now(UTC),
                )
            )  # fmt: skip
            self.db.flush()

    def series(self, code, values, start=1):
        """Consecutive months from `start`; each value's previous is the one before it."""
        for offset, value in enumerate(values):
            before = values[offset - 1] if offset else None
            self.put(code, date(2026, start + offset, 1), value, before)


def run(db, business):
    with scoped(db, business):
        return detection.detect(db, owner_tenant(db, business))


def changes(api, business, query="", who="owner", org=0):
    res = api.get(f"{ORGS}/{business[org]}/changes{query}", headers=business[2][who])
    assert res.status_code == 200, res.text
    return res.json()


def by_kpi(events, code):
    return [e for e in events if e["kpi_code"] == code]


@pytest.fixture
def shop(db, business):
    """Four months of hand-picked figures, with the change in each worked out below.

    revenue          1000, 1100, 770, 1000   +10% (no), -30% (major, bad), +29.87% (notable, good)
    net_margin_pct   10, 12, 7, 15           +2 (no), -5 points (notable, bad), +8 (major, good)
    operating_expenses 1000, 1000, 1000, 1200   +20% in April: notable, and bad (lower is better)
    cost_of_goods_sold (neither good nor bad)  500, 500, 500, 700   +40% in April: major, neutral
    """
    values = Values(db, business)
    values.series("revenue", [1000, 1100, 770, 1000])
    values.series("net_margin_pct", [10, 12, 7, 15])
    values.series("operating_expenses", [1000, 1000, 1000, 1200])
    values.series("cost_of_goods_sold", [500, 500, 500, 700])
    run(db, business)
    return business


def test_only_changes_bigger_than_normal_are_found(api, shop):
    events = changes(api, shop)
    assert {(e["kpi_code"], e["period_start"]) for e in events} == {
        ("revenue", "2026-03-01"), ("revenue", "2026-04-01"),
        ("net_margin_pct", "2026-03-01"), ("net_margin_pct", "2026-04-01"),
        ("operating_expenses", "2026-04-01"), ("cost_of_goods_sold", "2026-04-01"),
    }  # fmt: skip


def test_a_fall_in_sales_is_a_major_piece_of_bad_news_with_the_numbers_behind_it(api, shop):
    event = by_kpi(changes(api, shop, "?month=2026-03-01"), "revenue")[0]
    assert (event["direction"], event["severity"], event["effect"]) == ("down", "major", "bad")
    assert (event["value"], event["reference_value"]) == ("770.00", "1100.00")
    assert (event["change"], event["change_unit"]) == ("-30.0", "percent")
    assert event["summary"] == (
        f"{event['kpi_name']} fell by 30% in March 2026: £770.00, "
        "down from £1,100.00 in February 2026."
    )
    assert event["kind"] == "material_change" and event["status"] == "open"
    assert event["period_end"] == "2026-03-31" and not event["explained_by_season"]


def test_a_rise_in_sales_is_good_news(api, shop):
    event = by_kpi(changes(api, shop, "?month=2026-04-01"), "revenue")[0]
    assert (event["direction"], event["severity"], event["effect"]) == ("up", "notable", "good")
    assert event["change"] == "29.9"


def test_a_margin_is_judged_in_percentage_points_not_per_cent(api, shop):
    margin = by_kpi(changes(api, shop), "net_margin_pct")
    march = next(e for e in margin if e["period_start"] == "2026-03-01")
    april = next(e for e in margin if e["period_start"] == "2026-04-01")
    assert (march["change"], march["change_unit"], march["severity"]) == (
        "-5.0",
        "points",
        "notable",
    )
    assert (april["change"], april["severity"], april["effect"]) == ("8.0", "major", "good")
    assert "fell by 5.0 points" in march["summary"]


def test_a_rise_in_costs_is_bad_news_and_a_figure_with_no_good_direction_is_neutral(api, shop):
    april = changes(api, shop, "?month=2026-04-01")
    assert by_kpi(april, "operating_expenses")[0]["effect"] == "bad"
    cogs = by_kpi(april, "cost_of_goods_sold")[0]
    assert (cogs["effect"], cogs["severity"]) == ("neutral", "major")


def test_the_biggest_changes_come_first_within_a_month_and_the_newest_month_first(api, shop):
    events = changes(api, shop)
    assert events[0]["period_start"] == "2026-04-01" and events[-1]["period_start"] == "2026-03-01"
    april = [e for e in events if e["period_start"] == "2026-04-01"]
    assert [e["severity"] for e in april] == sorted(
        (e["severity"] for e in april), key=["major", "notable"].index
    )
    majors = [e for e in april if e["severity"] == "major"]
    assert [e["kpi_code"] for e in majors] == [
        "cost_of_goods_sold",
        "net_margin_pct",
    ]  # +40%, +8 points


def test_within_the_same_size_band_the_bigger_change_comes_first(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 2, 1), 1400, 1000)  # +40%
    values.put("average_order_value", date(2026, 2, 1), 131, 100)  # +31%, and sorts first by name
    run(db, business)
    assert [e["kpi_code"] for e in changes(api, business)] == ["revenue", "average_order_value"]


def test_the_list_can_be_filtered(api, shop):
    assert {e["effect"] for e in changes(api, shop, "?effect=bad")} == {"bad"}
    assert {e["severity"] for e in changes(api, shop, "?severity=major")} == {"major"}
    assert len(changes(api, shop, "?month=2026-03-01")) == 2
    assert len(changes(api, shop, "?limit=3")) == 3
    assert (
        api.get(f"{ORGS}/{shop[0]}/changes?effect=lovely", headers=shop[2]["owner"]).status_code
        == 422
    )


def test_one_change_can_be_opened_by_its_id(api, shop):
    event = changes(api, shop)[0]
    res = api.get(f"{ORGS}/{shop[0]}/changes/{event['id']}", headers=shop[2]["owner"])
    assert res.status_code == 200 and res.json() == event
    missing = api.get(f"{ORGS}/{shop[0]}/changes/{uuid.uuid4()}", headers=shop[2]["owner"])
    assert missing.status_code == 404


def test_a_month_still_in_progress_is_not_judged(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 1, 1), 1000, None)
    values.put("revenue", date(2026, 2, 1), 400, 1000, complete=False)
    run(db, business)
    assert changes(api, business) == []


def test_a_figure_that_could_not_be_worked_out_is_not_judged(api, db, business):
    values = Values(db, business)
    values.put("net_margin_pct", date(2026, 1, 1), 10, None)
    values.put("net_margin_pct", date(2026, 2, 1), None, 10, status="undefined")
    values.put("revenue", date(2026, 3, 1), 500, None)  # nothing before it to compare with
    run(db, business)
    assert changes(api, business) == []


def test_a_change_from_a_tiny_starting_point_is_left_alone(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 1, 1), 50, None)
    values.put("revenue", date(2026, 2, 1), 500, 50)  # +900%, but from £50
    run(db, business)
    assert changes(api, business) == []


def test_the_data_quality_of_the_month_is_carried_along(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 1, 1), 1000, None)
    values.put("revenue", date(2026, 2, 1), 500, 1000, quality=55)
    run(db, business)
    assert changes(api, business)[0]["data_quality"] == 55


def test_nothing_in_the_data_gives_nothing_back(api, business):
    assert changes(api, business) == []


# --- seasons --------------------------------------------------------------------------------


def with_summer(db, business, pct=50):
    with scoped(db, business):
        db.add(season("Summer", (8, 1), (8, 31), pct, source="user", status="active"))
        db.flush()


def test_a_rise_the_owners_season_leads_them_to_expect_is_marked_as_expected(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1450, 1000)  # +45% in a month that is 50% busier
    values.put("revenue", date(2026, 9, 1), 1000, 1450)  # -31%; back to normal after +50%: -33%
    with_summer(db, business)
    run(db, business)
    august = by_kpi(changes(api, business, "?month=2026-08-01"), "revenue")[0]
    september = by_kpi(changes(api, business, "?month=2026-09-01"), "revenue")[0]
    assert august["explained_by_season"] and september["explained_by_season"]
    assert august["summary"].endswith("would lead you to expect.")
    assert august["severity"] == "major"  # still shown, still the size it was


def test_a_change_far_bigger_than_the_season_explains_is_not_marked_as_expected(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1800, 1000)  # +80%; the season only accounts for 50
    with_summer(db, business)
    run(db, business)
    assert not changes(api, business)[0]["explained_by_season"]


def test_seasons_do_not_excuse_a_change_in_the_wrong_direction(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 600, 1000)  # a busy month, and sales fell
    with_summer(db, business)
    run(db, business)
    assert not changes(api, business)[0]["explained_by_season"]


def test_seasons_only_excuse_figures_that_follow_the_trading_year(api, db, business):
    values = Values(db, business)
    values.put("average_order_value", date(2026, 8, 1), 150, 100)  # +50% in a busy month
    values.put("net_margin_pct", date(2026, 8, 1), 20, 10)
    with_summer(db, business)
    run(db, business)
    assert not any(e["explained_by_season"] for e in changes(api, business))


def test_a_change_measured_in_points_is_never_excused_by_a_season(api, db, business):
    db.execute(
        HealthRule.__table__.update()
        .where(HealthRule.kpi_code == "net_margin_pct")
        .values(seasonal=True)
    )
    values = Values(db, business)
    values.put("net_margin_pct", date(2026, 8, 1), 20, 10)  # +10 points in a busy month
    with_summer(db, business)
    run(db, business)
    assert not changes(api, business)[0]["explained_by_season"]


def test_a_season_the_owner_has_not_confirmed_excuses_nothing(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1500, 1000)
    with scoped(db, business):
        db.add(season("Summer?", (8, 1), (8, 31), 50, source="detected", status="suggested"))
        db.flush()
    run(db, business)
    assert not changes(api, business)[0]["explained_by_season"]


def test_expected_changes_can_be_left_out_of_the_list(api, db, business):
    values = Values(db, business)
    values.put("revenue", date(2026, 8, 1), 1500, 1000)
    values.put("net_margin_pct", date(2026, 8, 1), 20, 10)
    with_summer(db, business)
    run(db, business)
    assert len(changes(api, business)) == 2
    only = changes(api, business, "?include_expected=false")
    assert [e["kpi_code"] for e in only] == ["net_margin_pct"]


# --- running it again, and who can see what -------------------------------------------------


def test_running_it_again_changes_nothing_and_keeps_what_was_done_with_an_event(api, db, shop):
    first = changes(api, shop)
    with scoped(db, shop):
        event = db.scalars(select(DetectionEvent).limit(1)).one()
        event_id = event.id
        event.status = "dismissed"
        db.commit()
    again = run(db, shop)
    assert (again.found, again.removed) == (len(first), 0)
    after = changes(api, shop)
    assert len(after) == len(first)
    assert {e["id"] for e in after} == {e["id"] for e in first}  # the same rows, not new ones
    assert next(e for e in after if e["id"] == str(event_id))["status"] == "dismissed"


def test_a_change_that_is_no_longer_true_is_removed(api, db, shop):
    with scoped(db, shop):
        revenue = select(KpiDefinition.id).where(KpiDefinition.code == "revenue")
        db.execute(
            KpiValue.__table__.update()
            .where(KpiValue.period_start == date(2026, 3, 1), KpiValue.kpi_id.in_(revenue))
            .values(value=D("1090"))  # sales were steady after all
        )
    result = run(db, shop)
    assert result.removed == 1
    march = changes(api, shop, "?month=2026-03-01")
    assert [e["kpi_code"] for e in march] == ["net_margin_pct"]


def test_a_change_is_updated_when_the_figures_are_recalculated(api, db, shop):
    with scoped(db, shop):
        db.execute(
            KpiValue.__table__.update()
            .where(KpiValue.period_start == date(2026, 3, 1), KpiValue.value == D("770"))
            .values(value=D("660"))
        )
    run(db, shop)
    event = by_kpi(changes(api, shop, "?month=2026-03-01"), "revenue")[0]
    assert event["value"] == "660.00" and event["change"] == "-40.0"


def test_each_business_sees_only_its_own_changes(api, db, shop):
    assert changes(api, shop, who="other", org=1) == []
    assert api.get(f"{ORGS}/{shop[1]}/changes", headers=shop[2]["owner"]).status_code == 404
    with scoped(db, shop, 1):
        assert db.scalar(select(func.count()).select_from(DetectionEvent)) == 0


def test_viewers_can_see_changes_and_strangers_cannot(api, shop):
    assert changes(api, shop, who="viewer")
    assert api.get(f"{ORGS}/{shop[0]}/changes").status_code == 401


# --- looking for changes happens whenever the figures are worked out ------------------------


def test_changes_are_looked_for_whenever_the_kpis_are_worked_out(api, db, business, storage):
    from app.services import jobs
    from tests.test_kpi_engine import sale

    sale(db, business, date(2026, 1, 10), "500", cost="200")
    sale(db, business, date(2026, 2, 10), "250", cost="100")  # sales halve in February
    api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    ran = jobs.work_once(db, "detect-worker", storage=storage)
    assert ran.status == "succeeded" and ran.kind == "kpi.calculate"
    february = by_kpi(changes(api, business, "?month=2026-02-01"), "revenue")
    assert february and february[0]["direction"] == "down" and february[0]["change"] == "-50.0"


def test_a_problem_looking_for_changes_does_not_hide_that_the_kpis_were_done(
    api, db, business, storage, monkeypatch
):
    from app.services import jobs
    from tests.test_kpi_engine import sale

    sale(db, business, date(2026, 1, 10), "100", cost="40")

    def boom(*args, **kwargs):
        raise RuntimeError("detection broke")

    monkeypatch.setattr(detection, "detect", boom)
    api.post(f"{ORGS}/{business[0]}/kpis/calculate", headers=business[2]["owner"])
    ran = jobs.work_once(db, "w", storage=storage)
    assert ran.status == "succeeded" and ran.result["status"] == "succeeded"


# --- the tables for diagnoses are in place, and keep each business's data apart -------------


def _event(db, business, org=0):
    with scoped(db, business, org):
        kpi_id = db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == "revenue")).one()
        event = DetectionEvent(
            kpi_id=kpi_id, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
            direction="down", severity="major", effect="bad", value=D("770"),
            reference_value=D("1100"), change=D("-30"), change_unit="percent",
            summary="Sales fell.",
            detected_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(event)
        db.flush()
        return event.id


def test_a_diagnosis_holds_evidence_of_four_kinds_kept_apart(db, business):
    event_id = _event(db, business)
    with scoped(db, business):
        diagnosis = Diagnosis(
            event_id=event_id, status="ready", headline="Fewer sales of bread",
            summary="Bread sales fell.", confidence=80, confidence_label="high",
            diagnosed_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(diagnosis)
        db.flush()
        for order, kind in enumerate(("fact", "statistical", "ai_interpretation", "insufficient")):
            db.add(
                DiagnosticEvidence(
                    diagnosis_id=diagnosis.id,
                    evidence_type=kind,
                    statement=f"A {kind}.",
                    sort_order=order,
                )
            )
        db.flush()
        kinds = db.scalars(
            select(DiagnosticEvidence.evidence_type).order_by(DiagnosticEvidence.sort_order)
        ).all()
    assert kinds == ["fact", "statistical", "ai_interpretation", "insufficient"]


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "rumour"}, {"direction": "left"}, {"severity": "huge"}, {"effect": "lovely"},
        {"change_unit": "pounds"}, {"status": "lost"}, {"summary": "  "}, {"data_quality": 101},
        {"period_end": date(2026, 2, 28)}, {"granularity": "week"},
    ],
)  # fmt: skip
def test_an_event_that_makes_no_sense_is_refused(db, business, bad):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), scoped(db, business):
        kpi_id = db.scalars(select(KpiDefinition.id).where(KpiDefinition.code == "revenue")).one()
        fields = dict(
            kpi_id=kpi_id, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
            direction="down", severity="major", effect="bad", value=D("770"),
            reference_value=D("1100"), change=D("-30"), change_unit="percent",
            summary="Sales fell.",
            detected_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(DetectionEvent(**{**fields, **bad}))
        db.flush()


def test_there_is_one_event_per_figure_and_month(db, business):
    from sqlalchemy.exc import IntegrityError

    _event(db, business)
    with pytest.raises(IntegrityError):
        _event(db, business)


def test_a_diagnosis_is_either_confident_or_says_there_is_not_enough_evidence(db, business):
    from sqlalchemy.exc import IntegrityError

    event_id = _event(db, business)
    with pytest.raises(IntegrityError), scoped(db, business):
        db.add(
            Diagnosis(
                event_id=event_id, status="ready", headline="Unsure", summary="Not sure.",
                confidence=None, confidence_label="insufficient", diagnosed_at=datetime.now(UTC),
            )
        )  # fmt: skip
        db.flush()


def test_evidence_cannot_be_attached_to_another_businesss_diagnosis(db, business):
    from sqlalchemy.exc import IntegrityError

    event_id = _event(db, business)
    with scoped(db, business):
        diagnosis = Diagnosis(
            event_id=event_id, status="ready", headline="Fewer sales", summary="Fewer.",
            confidence=60, confidence_label="medium", diagnosed_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(diagnosis)
        db.flush()
        diagnosis_id = diagnosis.id
    with pytest.raises(IntegrityError), scoped(db, business, 1):
        db.add(DiagnosticEvidence(diagnosis_id=diagnosis_id, evidence_type="fact", statement="X."))
        db.flush()


def test_removing_an_event_removes_its_diagnosis_and_evidence(db, business):
    event_id = _event(db, business)
    with scoped(db, business):
        diagnosis = Diagnosis(
            event_id=event_id, status="ready", headline="Fewer sales", summary="Fewer.",
            confidence=60, confidence_label="medium", diagnosed_at=datetime.now(UTC),
        )  # fmt: skip
        db.add(diagnosis)
        db.flush()
        db.add(DiagnosticEvidence(diagnosis_id=diagnosis.id, evidence_type="fact", statement="X."))
        db.flush()
        db.execute(DetectionEvent.__table__.delete())
        assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0
        assert db.scalar(select(func.count()).select_from(DiagnosticEvidence)) == 0
