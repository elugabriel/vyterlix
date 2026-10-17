# ruff: noqa: E501
"""The What changed page says each change in everyday words (client feedback §2: plain wording)."""

from datetime import date

from app.plain.changes import explain_change
from tests.test_detection import changes


def anomaly(**over):
    base = dict(
        kpi_code="gross_margin_pct", kpi_name="Profit margin", unit="percent", direction="down",
        severity="major", effect="bad", period_start=date(2026, 9, 1), value="-245.80",
        reference_value="78.40", change="-324.2", change_unit="points", explained_by_season=False,
        kind="anomaly",
    )  # fmt: skip
    return explain_change(**{**base, **over})


def test_an_unusual_month_is_said_against_what_is_normal_not_against_last_month():
    words = anomaly()
    assert words.headline == "Profit margin was unusually low in September 2026."
    assert words.numbers == "Profit margin was -245.8%. It is normally around 78.4%."
    assert words.happened == "Profit margin was lower than it normally is for your business."


def test_poor_data_is_said_without_a_score():
    assert (
        anomaly(data_quality=75).data_note
        == "Some of the figures behind this month are missing, so treat this with care."
    )
    assert anomaly(data_quality=95).data_note is None and anomaly().data_note is None


def test_every_change_on_the_page_carries_its_words(api, march):
    events = changes(api, march)
    assert events
    for event in events:
        words = event["plain"]
        assert words["headline"] and words["happened"] and words["numbers"] and words["why"]
        assert "points below your usual" not in " ".join(
            str(v) for v in words.values()
        )  # the engine's own wording
        assert words["tone"] in ("good", "bad", "watch", "neutral", "unknown")


def test_a_fall_in_sales_reads_like_the_alert_card(api, march):
    sales = next(
        e
        for e in changes(api, march)
        if e["kpi_code"] == "revenue" and e["kind"] == "material_change"
    )
    assert (
        sales["plain"]["headline"].startswith("Sales fell")
        and sales["plain"]["happened"] == "You sold less than last month."
    )
