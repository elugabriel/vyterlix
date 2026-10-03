"""The arithmetic and wording of business health, kept free of the database so it can be tested
exhaustively. A metric is scored 0 to 100 from three anchors (bad / ok / good); metrics roll up
into an area, and areas roll up into the overall score, each as a weighted average.
"""

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
OK_SCORE = Decimal("60")  # the score at the "ok" anchor
BASELINE_PERIODS = 6  # how many earlier months make up "usual"
MIN_BASELINE_PERIODS = 3  # fewer than this and we don't know what is usual yet
MIN_COVERAGE = 40  # below this share of the weighted picture, no overall score is given
TREND_POINTS = 3  # a change smaller than this is "flat"

CATEGORY_LABEL = {
    "financial": "Money",
    "sales": "Sales",
    "customer": "Customers",
    "marketing": "Marketing",
    "inventory": "Stock",
    "operational": "Operations",
}
STATUS_LABEL = {
    "healthy": "healthy",
    "fair": "fair",
    "needs_attention": "needs attention",
    "at_risk": "at risk",
    "not_enough_data": "not enough data",
}


def to_int(value: Decimal) -> int:
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def score_metric(
    value: Decimal, direction: str, bad: Decimal, ok: Decimal, good: Decimal
) -> Decimal:
    """0 at `bad`, 60 at `ok`, 100 at `good`, straight lines between, flat beyond the ends."""
    if direction == "down_good":  # mirror it, so lower really is better
        value, bad, ok, good = -value, -bad, -ok, -good
    if value <= bad:
        return ZERO
    if value >= good:
        return HUNDRED
    if value <= ok:
        return OK_SCORE * (value - bad) / (ok - bad)
    return OK_SCORE + (HUNDRED - OK_SCORE) * (value - ok) / (good - ok)


def status_for(score: int | Decimal | None) -> str:
    if score is None:
        return "not_enough_data"
    if score >= 80:
        return "healthy"
    if score >= 60:
        return "fair"
    if score >= 40:
        return "needs_attention"
    return "at_risk"


def trend_for(score: int | None, previous: int | None) -> str | None:
    """up / down / flat compared with the period before, or None with nothing to compare."""
    if score is None or previous is None:
        return None
    change = score - previous
    if change >= TREND_POINTS:
        return "up"
    if change <= -TREND_POINTS:
        return "down"
    return "flat"


def baseline_of(values: list[Decimal]) -> Decimal | None:
    """What is usual: the average of the earlier months, once there are enough of them."""
    if len(values) < MIN_BASELINE_PERIODS:
        return None
    return sum(values, ZERO) / len(values)


def percent_from(value: Decimal, baseline: Decimal) -> Decimal | None:
    """How far above (+) or below (-) the usual level, as a percentage. None if usual is zero."""
    if baseline == 0:
        return None
    return (value - baseline) / abs(baseline) * HUNDRED


def weighted_average(pairs: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    """[(score, weight), ...] -> the average, each score counting by its weight."""
    total = sum((w for _, w in pairs), ZERO)
    if total == 0:
        return None
    return sum((s * w for s, w in pairs), ZERO) / total


# --- wording -------------------------------------------------------------------------------------


def format_value(value: Decimal, unit: str) -> str:
    value = Decimal(value)
    if unit == "gbp":
        return f"£{value:,.2f}"
    if unit == "percent":
        return f"{value:,.1f}%"
    if unit == "ratio":
        return f"{value:,.2f} times"
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"


def describe_metric(
    name: str,
    unit: str,
    basis: str,
    direction: str,
    value: Decimal,
    score: Decimal,
    *,
    bad: Decimal,
    good: Decimal,
    baseline: Decimal | None = None,
    compared: Decimal | None = None,
    baseline_months: int = 0,
) -> str:
    """One plain sentence on a metric: what it was, what it is judged against, what it scored."""
    shown = format_value(value, unit)
    points = f"That scores {to_int(score)} out of 100."
    if basis == "vs_baseline" and baseline is not None and compared is not None:
        usual = format_value(baseline, unit)
        if abs(compared) < Decimal("0.5"):
            where = f"in line with your usual {usual}"
        else:
            side = "above" if compared > 0 else "below"
            where = f"{abs(compared):.0f}% {side} your usual {usual}"
        months = f"(the average of the last {baseline_months} months)"
        return f"{name}: {shown}, {where} {months}. {points}"
    good_text, bad_text = format_value(good, unit), format_value(bad, unit)
    if direction == "down_good":
        yardstick = f"A good level is {good_text} or less; above {bad_text} is a worry."
    else:
        yardstick = f"A good level is {good_text} or more; below {bad_text} is a worry."
    return f"{name}: {shown}. {yardstick} {points}"


def explain_component(
    category: str, score: int | None, metrics: list[dict], skipped: list[str]
) -> str:
    label = CATEGORY_LABEL[category]
    if score is None:
        why = "; ".join(skipped) if skipped else "nothing in this area can be measured yet"
        return f"{label} can't be scored yet: {why}."
    text = f"{label} scores {score} out of 100 ({STATUS_LABEL[status_for(score)]})."
    if len(metrics) >= 2:
        best = max(metrics, key=lambda m: (m["score"], m["weight"]))
        worst = min(metrics, key=lambda m: (m["score"], -m["weight"]))
        text += f" Strongest: {best['name']} ({to_int(Decimal(str(best['score'])))})."
        if worst["score"] < best["score"]:
            text += f" Weakest: {worst['name']} ({to_int(Decimal(str(worst['score'])))})."
    return text


def explain_overall(
    score: int | None, coverage: int, components: list[dict], unscored: list[str]
) -> str:
    scored = [c for c in components if c["score"] is not None]
    if score is None:
        return (
            "There isn't enough data yet to give an overall score. "
            f"Only {coverage}% of what we look at could be measured."
        )
    text = f"Your business health is {score} out of 100 ({STATUS_LABEL[status_for(score)]})."
    if len(scored) >= 2:
        # Ties go to the area that counts for more, so the answer never depends on list order.
        best = max(scored, key=lambda c: (c["score"], c["weight"]))
        worst = min(scored, key=lambda c: (c["score"], -c["weight"]))
        text += f" Strongest area: {CATEGORY_LABEL[best['category']]} ({best['score']})."
        if worst["score"] < best["score"]:
            text += f" Weakest: {CATEGORY_LABEL[worst['category']]} ({worst['score']})."
    if unscored:
        names = ", ".join(CATEGORY_LABEL[c] for c in unscored)
        text += f" Not yet counted: {names} (no data for it yet)."
    if coverage < 100:
        text += f" The score covers {coverage}% of what we look at."
    return text
