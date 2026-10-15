# ruff: noqa: E501
"""How urgent something is, in three words and a reason (client feedback 15/10/2026, section 5).

She asked that priority reflects the likely business impact, the urgency and the strength of the
evidence, not just the size of a percentage change. So a suggestion's own scores decide it:

    score = 40% impact + 30% urgency + 30% evidence      (each 0 to 100)
    High at 65 or more, Medium at 40 or more, otherwise Low

Two things hold it back: an alert that is itself only low-key can be at most Medium, and thin
evidence (below 40) can be at most Medium however big the impact looks, because a guess should not
shout. Good news is always Low. With no suggestion to score, the alert's own seriousness decides.

Pure rules, no database access.
"""

from dataclasses import dataclass
from typing import Literal

Level = Literal["low", "medium", "high"]
WEIGHTS = {"impact": 40, "urgency": 30, "confidence": 30}
HIGH_AT = 65
MEDIUM_AT = 40
THIN_EVIDENCE = 40
LABEL = {"low": "Low", "medium": "Medium", "high": "High"}
BY_SEVERITY: dict[str, Level] = {
    "critical": "high",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


@dataclass(frozen=True)
class Priority:
    level: Level
    reason: str


def _band(score: int, strong: str, middle: str, weak: str) -> str:
    return strong if score >= 66 else middle if score >= MEDIUM_AT else weak


def prioritise(*, alert_severity: str, effect: str, scores: dict[str, int] | None) -> Priority:
    """`scores` is the top suggestion's impact, urgency and confidence (0 to 100), or None."""
    if effect == "good":
        return Priority("low", "This is good news, so nothing needs fixing.")
    by_alert = BY_SEVERITY.get(alert_severity, "medium")
    if not scores or any(k not in scores for k in WEIGHTS):
        why = {
            "high": "a big change for your business",
            "medium": "a change worth a look",
            "low": "a small change",
        }[by_alert]
        return Priority(
            by_alert, f"{LABEL[by_alert]} priority: {why}. We have no suggestion to score yet."
        )

    total = sum(scores[k] * w for k, w in WEIGHTS.items()) / 100
    level: Level = "high" if total >= HIGH_AT else "medium" if total >= MEDIUM_AT else "low"
    held = None
    if by_alert == "low" and level == "high":
        level, held = "medium", "the alert itself is low-key"
    if scores["confidence"] < THIN_EVIDENCE and level == "high":
        level, held = "medium", "the evidence is thin"
    parts = [
        _band(
            scores["impact"],
            "it could have a big effect on your business",
            "it could have a moderate effect on your business",
            "it is likely to have a small effect",
        ),
        _band(scores["urgency"], "it is urgent", "it is fairly urgent", "it can wait a little"),
        _band(
            scores["confidence"],
            "the evidence is strong",
            "the evidence is fair",
            "the evidence is thin",
        ),
    ]
    reason = f"{LABEL[level]} priority: {parts[0]}, {parts[1]}, and {parts[2]}."
    if held:
        reason += f" It is held at Medium because {held}."
    return Priority(level, reason)
