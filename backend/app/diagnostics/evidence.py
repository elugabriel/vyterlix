"""Turning what was found into a diagnosis: the evidence, the headline, and how sure we are.

Pure rules, no database (services/diagnosis.py gathers the inputs and saves the result).

Every statement is typed, so nothing is ever presented as more than it is:

- fact: a figure read straight from the business's own records;
- statistical: something worked out from the figures by arithmetic (a split of a change into
  causes, how far from usual a month was, what the seasons lead you to expect);
- ai_interpretation: a reading by the AI assistant (none are produced here; the assistant comes
  in a later phase and is always shown as such);
- insufficient: the engine saying it cannot tell, and why.

How sure the diagnosis is comes from rules written down here (Vyterlix's own starting values),
never from a model's opinion, and the diagnosis says how it got there.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.diagnostics.drivers import MIN_CONTRIBUTOR_SHARE
from app.health.scoring import format_value

RULES_VERSION = "diagnosis-1"
STRONG_SHARE = Decimal("50")  # a cause this large is a main cause
HIGH_CONFIDENCE = 75
MEDIUM_CONFIDENCE = 50
MAX_CONFIDENCE = 95  # nothing worked out from records alone is certain
DATA_QUALITY_OK = 80  # below this, the data behind the month is called out
NO_DETAIL_PENALTY = 15
EXPLAINED_WEIGHT = Decimal("0.6")
QUALITY_WEIGHT = Decimal("0.4")
CORROBORATION_BONUS = 10  # per further independent reading that points the same way
MAX_CORROBORATION = 2
MAIN_CAUSES_IN_HEADLINE = 2
MAX_SUMMARY_CAUSES = 3


@dataclass
class Item:
    evidence_type: str
    statement: str
    data: dict


@dataclass
class Confidence:
    score: int | None  # None when there is not enough evidence to say
    label: str  # high | medium | low | insufficient
    status: str  # ready | insufficient_evidence
    note: str


def _strength(finding) -> Decimal:
    return abs(Decimal(finding.share_pct)) if finding.share_pct is not None else Decimal("0")


def _to_int(value: Decimal) -> int:
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- the facts and the statistics --------------------------------------------------------------


def figure_facts(
    kind: str,
    name: str,
    unit: str,
    month: str,
    before: str,
    value: Decimal,
    reference: Decimal,
    data_quality: int | None,
) -> list[Item]:
    """What the records say about the figure itself, and how complete they are. For an unusual
    month the reference is the figure's usual level; otherwise it is the month before."""
    shown, ref = format_value(value, unit), format_value(reference, unit)
    if kind == "anomaly":
        statement = f"{name} was {shown} in {month}; its usual is {ref}."
    else:
        statement = f"{name} was {shown} in {month} and {ref} in {before}."
    facts = [Item("fact", statement, {"value": str(value), "compared_with": str(reference)})]
    if data_quality is not None and data_quality < DATA_QUALITY_OK:
        facts.append(
            Item(
                "fact",
                f"The data behind {month} is incomplete (a data-quality score of {data_quality} "
                "out of 100), so these figures may not be the whole picture.",
                {"data_quality": data_quality},
            )
        )
    return facts


def finding_item(finding) -> Item:
    """A driver is a fact when it is read straight off the records (a part that moved) and
    statistical when arithmetic produced it (a split into days, orders, or price and volume)."""
    kind = "fact" if finding.lens == "parts" else "statistical"
    return Item(
        kind,
        finding.text,
        {
            "kind": finding.kind,
            "lens": finding.lens,
            "label": finding.label,
            "amount": finding.amount,
            "share_pct": finding.share_pct,
        },
    )


def history_item(spreads: str, usual: str, months: int, month: str) -> Item:
    """For an unusual month: how far from normal it was."""
    return Item(
        "statistical",
        f"{month} was {abs(Decimal(spreads)):.1f} times further from the middle of the last "
        f"{months} months than this figure normally strays.",
        {"spreads_from_usual": spreads, "usual": usual, "history_months": months},
    )


def season_item(expected_pct: str, month: str) -> Item:
    return Item(
        "statistical",
        f"The busy and quiet seasons you entered lead you to expect a change of about "
        f"{Decimal(expected_pct):+.0f}% in {month}.",
        {"expected_season_change_pct": expected_pct},
    )


def limits(*, explainable: bool, findings: list, no_detail_share: Decimal | None) -> list[Item]:
    """The things the engine cannot tell, said plainly."""
    items = []
    if not explainable:
        items.append(
            Item(
                "insufficient",
                "Vyterlix cannot yet break this figure down into products, channels or other "
                "parts, so it cannot say what caused the change.",
                {"reason": "not_explainable"},
            )
        )
    elif not any(_strength(f) >= MIN_CONTRIBUTOR_SHARE for f in findings):
        items.append(
            Item(
                "insufficient",
                "No single cause stands out: the change is spread across many parts of the "
                "business, so it cannot be pinned on one.",
                {"reason": "spread_widely"},
            )
        )
    if no_detail_share is not None and no_detail_share >= MIN_CONTRIBUTOR_SHARE:
        items.append(
            Item(
                "insufficient",
                f"{no_detail_share:.0f}% of the change is in sales recorded without product "
                "detail, so that part cannot be traced to products.",
                {"reason": "no_product_detail", "share_pct": str(no_detail_share)},
            )
        )
    items.append(
        Item(
            "insufficient",
            "Vyterlix only sees the records you have given it, so it cannot tell whether "
            "things outside them (weather, competitors, marketing, local events) played a part.",
            {"reason": "outside_the_records"},
        )
    )
    return items


# --- the headline --------------------------------------------------------------------------------


def lead_clause(
    kind: str, name: str, unit: str, month: str, change: Decimal, change_unit: str, direction: str
) -> str:
    """The opening of the headline: what happened."""
    size = abs(Decimal(change))
    how = f"{size:.1f} points" if change_unit == "points" else f"{size:.0f}%"
    if kind == "anomaly":
        side = "above" if direction == "up" else "below"
        return f"{name} in {month} was {how} {side} its usual"
    verb = "rose" if direction == "up" else "fell"
    return f"{name} {verb} by {how} in {month}"


def cause_phrase(finding) -> str:
    """A finding as the end of a sentence: 'because ...'."""
    amount = Decimal(finding.amount)
    up = amount > 0
    kind = finding.kind
    if kind == "sales_count":
        return "there were " + ("more" if up else "fewer") + " sales"
    if kind == "sale_value":
        return "the average sale was " + ("bigger" if up else "smaller")
    if kind == "volume":
        return ("more" if up else "fewer") + " items were sold"
    if kind == "price":
        return "the prices charged were " + ("higher" if up else "lower")
    if kind == "calendar_days":
        return "the month had " + ("more" if up else "fewer") + " days"
    if kind == "daily_rate":
        return "an average day brought in " + ("more" if up else "less")
    if kind == "no_product_detail":
        return "sales recorded without product detail changed"
    # a part: "Product: Sourdough" -> "Sourdough fell"
    part = finding.label.split(": ", 1)[-1]
    return f"{part} " + ("rose" if up else "fell")


def headline(lead: str, findings: list, status: str) -> str:
    if status == "insufficient_evidence":
        return f"{lead}, but there is not enough evidence to say why."
    main = [f for f in findings if _strength(f) >= STRONG_SHARE][:MAIN_CAUSES_IN_HEADLINE]
    if not main:
        main = findings[:1]
    reasons = " and ".join(cause_phrase(f) for f in main)
    return f"{lead}, mainly because {reasons}."


def summary_of(headline_text: str, findings: list, note: str) -> str:
    """The headline, then the strongest causes that account for a real share of the change (at
    most three, strongest first), then how sure we are."""
    causes = [f.text for f in findings if _strength(f) >= MIN_CONTRIBUTOR_SHARE]
    return " ".join([headline_text, *causes[:MAX_SUMMARY_CAUSES], note])


# --- how sure are we -----------------------------------------------------------------------------


def confidence(findings: list, data_quality: int | None, explainable: bool) -> Confidence:
    """How well the evidence pins down a cause, from rules that can be read and checked.

    60% from how much of the change the strongest cause accounts for (capped at all of it), 40%
    from how complete the data is, plus a little when independent readings (days, orders, price
    and volume, a single part) point the same way, less when the cause is hidden in sales with
    no product detail. Never above 95: records alone do not prove a cause.
    """
    explained = max((_strength(f) for f in findings), default=Decimal("0"))
    if not explainable or explained < MIN_CONTRIBUTOR_SHARE:
        return Confidence(
            None,
            "insufficient",
            "insufficient_evidence",
            "No cause accounts for enough of the change to say what happened.",
        )
    quality = Decimal(100 if data_quality is None else data_quality)
    lenses = {f.lens for f in findings if _strength(f) >= STRONG_SHARE}
    corroboration = min(max(len(lenses) - 1, 0), MAX_CORROBORATION) * CORROBORATION_BONUS
    penalty = (
        NO_DETAIL_PENALTY
        if any(
            f.kind == "no_product_detail" and _strength(f) >= MIN_CONTRIBUTOR_SHARE
            for f in findings
        )
        else 0
    )
    raw = (
        EXPLAINED_WEIGHT * min(explained, Decimal("100"))
        + QUALITY_WEIGHT * quality
        + corroboration
        - penalty
    )
    score = min(MAX_CONFIDENCE, _to_int(raw))
    label = (
        "high" if score >= HIGH_CONFIDENCE else "medium" if score >= MEDIUM_CONFIDENCE else "low"
    )
    parts = [
        f"the strongest cause accounts for {min(explained, Decimal('100')):.0f}% of the change",
        f"the data is {quality:.0f} out of 100 complete",
    ]
    if corroboration:
        parts.append(f"{len(lenses)} different readings point the same way")
    if penalty:
        parts.append("some of the change sits in sales with no product detail")
    return Confidence(
        score, label, "ready", "This is " + label + " confidence because " + "; ".join(parts) + "."
    )
