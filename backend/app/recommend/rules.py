"""From "why did this happen" to "what should I do": candidate actions, scored and ranked.

Every step is a rule written down here, so any recommendation can be explained:

1. MATCH. A finding that is hurting the figure (it accounts for a real share of the change, in the
   same direction) calls up the library actions that answer that kind of finding.
2. SCORE. Each action is scored 0-100 on seven things: impact (how much of the change it could
   win back), confidence (how sure we are it is aimed at a real cause), fit with the owner's goals,
   urgency, ease (little effort and cost), and track record (neutral until outcomes are recorded).
3. RANK. Weighted points decide the order; ties go to the bigger impact, then the easier action.
4. EXPLAIN. The best action is recommended, with the reasons, and what put the runner-up behind.

These are Vyterlix's own starting rules. Nothing here is asked of a language model.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.health.scoring import format_value

RULES_VERSION = "recommend-1"
MIN_SHARE = Decimal("25")  # a finding must account for this share of the change to be acted on
MAX_OPTIONS = 5
FULL_MARKS_IMPACT = Decimal("0.5")  # winning back half the change earns full marks for impact
NO_DIAGNOSIS_CONFIDENCE = 40
NO_GOALS_FIT = 50  # a business with no goals set is neither helped nor hurt by goal fit
UNRELATED_GOAL_FIT = 20
RELATED_GOAL_FIT = 70
MATCHING_GOAL_FIT = 100
NEUTRAL_HISTORY = 50  # until outcomes are recorded (Phase 11) there is no track record to judge by
URGENCY = {"major": 90, "notable": 60}
FAST_DAYS, SLOW_DAYS, SPEED_ADJUSTMENT = 14, 60, 10
# Parts that are not a real product, customer, channel... (the engine's own catch-all rows). An
# action cannot be aimed at them, so a cause that is one of these is left out.
NOT_A_TARGET = {
    "No customer recorded",
    "No channel recorded",
    "No category",
    "No supplier",
    "Not linked to a product",
    "Unnamed",
    "Sales with no product detail",
    "Everything else",
}
EFFORT_WORDS = {"low": "low effort", "medium": "moderate effort", "high": "a lot of effort"}
COST_WORDS = {"none": "no cost", "low": "low cost", "medium": "moderate cost", "high": "high cost"}

WEIGHTS = {
    "impact": 30,
    "confidence": 20,
    "goal_fit": 15,
    "ease": 15,
    "urgency": 10,
    "history": 10,
}
LABELS = {
    "impact": "Impact",
    "confidence": "Confidence",
    "goal_fit": "Fit with your goals",
    "ease": "Ease",
    "urgency": "Urgency",
    "history": "Track record",
}
EFFORT_POINTS = {"low": 10, "medium": 30, "high": 55}
COST_POINTS = {"none": 0, "low": 10, "medium": 25, "high": 45}


@dataclass
class Finding:
    kind: str
    lens: str
    label: str
    amount: Decimal
    share_pct: Decimal | None
    text: str


@dataclass
class Goal:
    kpi_code: str | None
    goal_type: str
    title: str
    priority: int = 3


@dataclass
class Action:
    """One entry of the intervention library."""

    code: str
    name: str
    summary: str
    steps: list[str]
    addresses: list[dict]
    kpis: list[str]
    goal_types: list[str]
    effort: str
    cost_level: str
    days: int
    impact_share: Decimal


@dataclass
class Candidate:
    action: Action
    finding: Finding
    target: str | None  # the product, day, supplier... it is about
    gap: Decimal  # the size of the finding, in the figure's unit


@dataclass
class Scored:
    candidate: Candidate
    impact_value: Decimal
    scores: dict[str, int]  # impact, confidence, goal_fit, ease, urgency, history
    total: int
    goal_match: str | None = None  # the goal that made it fit, if any
    breakdown: list[dict] = field(default_factory=list)
    rank: int = 0


def _to_int(value: Decimal | float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- matching -----------------------------------------------------------------------------------


def strength(finding: Finding) -> Decimal:
    return abs(finding.share_pct) if finding.share_pct is not None else Decimal("0")


def hurts(finding: Finding) -> bool:
    """Does this finding account for a real share of the change, in the direction of the change?"""
    return finding.share_pct is not None and finding.share_pct >= MIN_SHARE


def part_of(finding: Finding) -> tuple[str | None, str | None]:
    """('Product', 'Sourdough') for a part named 'Product: Sourdough'; (None, None) otherwise."""
    if finding.kind != "contributor" or ": " not in finding.label:
        return None, None
    dimension, _, target = finding.label.partition(": ")
    return dimension, target


def matches(action: Action, finding: Finding, kpi_code: str) -> bool:
    if kpi_code not in action.kpis:
        return False
    dimension, _ = part_of(finding)
    return any(
        spec.get("kind") == finding.kind and spec.get("dimension") in (None, dimension)
        for spec in action.addresses
    )


def generate(library: list[Action], findings: list[Finding], kpi_code: str) -> list[Candidate]:
    """Every action the hurting findings call up; one per action and target."""
    seen: set[tuple[str, str | None]] = set()
    found = []
    for finding in findings:
        if not hurts(finding):
            continue
        _, target = part_of(finding)
        if target in NOT_A_TARGET:
            continue
        for action in library:
            key = (action.code, target)
            if key in seen or not matches(action, finding, kpi_code):
                continue
            seen.add(key)
            found.append(Candidate(action, finding, target, abs(finding.amount)))
    return found


# --- scoring ------------------------------------------------------------------------------------


def impact_score(impact_value: Decimal, event_change: Decimal) -> int:
    """How much of the whole change the action could win back, as marks out of 100 (half the change
    wins full marks)."""
    if event_change == 0:
        return 0
    share = abs(impact_value) / abs(event_change)
    return min(100, _to_int(share / FULL_MARKS_IMPACT * 100))


def confidence_score(diagnosis_confidence: int | None, finding: Finding) -> int:
    """Half how sure the diagnosis is, half how large a share of the change this finding is."""
    base = NO_DIAGNOSIS_CONFIDENCE if diagnosis_confidence is None else diagnosis_confidence
    return _to_int(
        Decimal(base) * Decimal("0.5") + min(strength(finding), Decimal(100)) * Decimal("0.5")
    )


def goal_fit(goals: list[Goal], kpi_code: str, action: Action) -> tuple[int, str | None]:
    """(how well it fits the owner's goals, the goal that made it fit)."""
    if not goals:
        return NO_GOALS_FIT, None
    best, why = UNRELATED_GOAL_FIT, None
    for goal in sorted(goals, key=lambda g: g.priority):
        if goal.kpi_code is not None and (
            goal.kpi_code == kpi_code or goal.kpi_code in action.kpis
        ):
            score = MATCHING_GOAL_FIT
        elif goal.goal_type in action.goal_types:
            score = RELATED_GOAL_FIT
        else:
            continue
        if score > best:
            best, why = score, goal.title
    return best, why


def urgency_score(severity: str, days_to_effect: int) -> int:
    base = URGENCY.get(severity, 50)
    if days_to_effect <= FAST_DAYS:
        base += SPEED_ADJUSTMENT
    elif days_to_effect > SLOW_DAYS:
        base -= SPEED_ADJUSTMENT
    return base


def ease_score(effort: str, cost_level: str) -> int:
    return 100 - EFFORT_POINTS[effort] - COST_POINTS[cost_level]


def total_score(scores: dict[str, int]) -> int:
    return _to_int(sum(Decimal(scores[k]) * w for k, w in WEIGHTS.items()) / sum(WEIGHTS.values()))


def evaluate(
    candidate: Candidate,
    *,
    kpi_code: str,
    event_change: Decimal,
    severity: str,
    diagnosis_confidence: int | None,
    goals: list[Goal],
    history: int = NEUTRAL_HISTORY,
) -> Scored:
    action = candidate.action
    impact_value = candidate.gap * action.impact_share
    fit, matched = goal_fit(goals, kpi_code, action)
    scores = {
        "impact": impact_score(impact_value, event_change),
        "confidence": confidence_score(diagnosis_confidence, candidate.finding),
        "goal_fit": fit,
        "ease": ease_score(action.effort, action.cost_level),
        "urgency": urgency_score(severity, action.days),
        "history": history,
    }
    total = total_score(scores)
    breakdown = [
        {
            "key": key,
            "label": LABELS[key],
            "score": scores[key],
            "weight": weight,
            "points": float(Decimal(scores[key] * weight) / Decimal(sum(WEIGHTS.values()))),
        }
        for key, weight in WEIGHTS.items()
    ]
    return Scored(candidate, impact_value, scores, total, matched, breakdown)


def rank(scored: list[Scored]) -> list[Scored]:
    """Best first. A tie goes to the bigger impact, then the easier action, then the name, so the
    order never depends on the order they were found in."""
    ordered = sorted(
        scored,
        key=lambda s: (-s.total, -s.impact_value, -s.scores["ease"], s.candidate.action.code),
    )
    for position, item in enumerate(ordered, start=1):
        item.rank = position
    return ordered


# --- wording ------------------------------------------------------------------------------------


def title_for(candidate: Candidate) -> str:
    action = candidate.action
    return action.name if candidate.target is None else f"{action.name}: {candidate.target}"


def description_for(candidate: Candidate, impact_value: Decimal, unit: str) -> str:
    text = f"{candidate.action.summary} Why this: {candidate.finding.text}"
    if impact_value > 0:
        text += (
            f" We estimate it could win back about {format_value(impact_value, unit)} "
            "(a starting estimate, not a promise)."
        )
    return text


def _best_at(item: Scored, other: Scored | None) -> str:
    """What this action does best (against the other one if there is one), as a phrase."""
    if other is None:
        key = max(WEIGHTS, key=lambda k: (item.scores[k] * WEIGHTS[k], k))
    else:
        key = max(WEIGHTS, key=lambda k: ((item.scores[k] - other.scores[k]) * WEIGHTS[k], k))
    phrases = {
        "impact": "it could win back the most",
        "confidence": "we are surest it is aimed at a real cause",
        "goal_fit": "it fits your goals best",
        "ease": "it takes the least effort and cost",
        "urgency": "it is the most urgent to act on",
        "history": "it has the best track record",
    }
    return phrases[key]


def _pulls_ahead_on(best: Scored, other: Scored) -> str:
    """The one thing the best action beats the other on by the most points."""
    key = max(WEIGHTS, key=lambda k: ((best.scores[k] - other.scores[k]) * WEIGHTS[k], k))
    return LABELS[key].lower()


def rationale(ordered: list[Scored], unit: str) -> str:
    """Why this one: the recommended action, what is behind it, and why the next is second."""
    best = ordered[0]
    candidate = best.candidate
    action = candidate.action
    parts = [
        f'We recommend "{title_for(candidate)}" ({best.total} out of 100) mainly because '
        f"{_best_at(best, ordered[1] if len(ordered) > 1 else None)}.",
        f"It is aimed at: {candidate.finding.text}",
    ]
    if best.impact_value > 0:
        parts.append(
            f"It could win back about {format_value(best.impact_value, unit)} "
            "(a starting estimate, not a promise)."
        )
    parts.append(
        f"It takes {EFFORT_WORDS[action.effort]} and {COST_WORDS[action.cost_level]}, and should "
        f"start to show in about {action.days} days."
    )
    if best.goal_match:
        parts.append(f"It fits your goal: {best.goal_match}.")
    if len(ordered) > 1:
        second = ordered[1]
        parts.append(
            f'Next best is "{title_for(second.candidate)}" ({second.total} out of 100); the '
            f"recommended action pulls ahead mainly on {_pulls_ahead_on(best, second)}."
        )
    return " ".join(parts)
