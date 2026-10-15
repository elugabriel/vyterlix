# ruff: noqa: E501
"""A change in a figure, said in everyday words (client feedback 15/10/2026, sections 2 and 5).

The detection engine records a change as numbers ("248.9 points below your usual 82.6%"). A small
business owner needs a headline, a sentence on what happened, and why it matters. This builds them
from the same figure wording as the key figures page (app/plain/figures.py), so the two never
disagree, and like that module it never states a cause: causes are on the What changed page, with
the evidence.

No database access: a change goes in, words come out.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.plain.figures import Tone, explain_figure


@dataclass(frozen=True)
class ChangeWords:
    headline: str  # "Sales fell sharply in September 2026."
    happened: str  # "You sold less than last month."
    numbers: str  # "Sales were £1,766.20, compared with £2,273.00 last month (down 22.3%)."
    why: str  # why it matters
    tone: Tone
    season: (
        str | None
    )  # set when the change is about what the owner's busy and quiet times lead them to expect


def _show(value: Decimal, unit: str) -> str:
    if unit == "gbp":
        return f"-£{abs(value):,.2f}" if value < 0 else f"£{value:,.2f}"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:.2f} times"


def explain_change(
    *,
    kpi_code: str,
    kpi_name: str,
    unit: str,
    direction: str,  # "up" or "down": which way the figure moved
    severity: str,  # "notable" or "major"
    effect: str,  # "good", "bad" or "neutral": what it means for the business
    period_start: date,
    value: str,
    reference_value: str,
    change: str,
    change_unit: str,  # "percent" or "points"
    explained_by_season: bool,
) -> ChangeWords:
    month = f"{period_start:%B %Y}"
    size = "sharply" if severity == "major" else "noticeably"
    moved = "rose" if direction == "up" else "fell"
    headline = f"{kpi_name} {moved} {size} in {month}."

    # Which way is good, so the same figure sentences give the right tone.
    if effect == "good":
        good_direction = "up_good" if direction == "up" else "down_good"
    elif effect == "bad":
        good_direction = "down_good" if direction == "up" else "up_good"
    else:
        good_direction = "neutral"
    plain = explain_figure(
        kpi_code,
        unit=unit,
        direction=good_direction,
        value=value,
        change_pct=change,
        previous_value=reference_value,
        granularity="month",
    )

    now, before, amount = Decimal(value), Decimal(reference_value), abs(Decimal(change))
    how_much = f"{amount:.1f} points" if change_unit == "points" else f"{amount:.1f}%"
    numbers = f"{kpi_name} was {_show(now, unit)}, compared with {_show(before, unit)} last month ({'up' if direction == 'up' else 'down'} {how_much})."

    if plain is None:  # a figure the wording table does not know: say only what is certain
        happened, why, tone = (
            f"{kpi_name} {moved} compared with last month.",
            "Open the explanation to see what is behind it.",
            "neutral",
        )
    else:
        happened, why, tone = plain.says, plain.why, plain.tone
    season = (
        "This is about what your busy and quiet times lead you to expect."
        if explained_by_season
        else None
    )
    return ChangeWords(headline, happened, numbers, why, tone, season)
