# ruff: noqa: E501
"""Keeping an answer honest: every number in it must come from what was looked up.

When an outside language model is allowed to put an answer into friendlier words, its wording is
checked here before anyone sees it. If it contains a figure that is not in the facts it was given, a link, or is too long, it is thrown away and the plain answer built from the facts is
used instead.
"""

import re

MAX_ANSWER_CHARS = 1800
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """The figures in a piece of text, written the same way whatever the formatting (£1,234.50 and
    1234.5 are the same figure; 12.0 and 12 are the same figure)."""
    found = set()
    for raw in _NUMBER.findall(text):
        cleaned = raw.replace(",", "")
        if "." in cleaned:
            cleaned = cleaned.rstrip("0").rstrip(".")
        found.add(cleaned or "0")
    return found


def ungrounded_numbers(answer: str, facts: list[str]) -> set[str]:
    """Figures in the answer that are not in the facts. A figure typed in the question does not
    count: the question is not a source."""
    allowed: set[str] = set()
    for fact in facts:
        allowed |= numbers_in(fact)
    return numbers_in(answer) - allowed


def acceptable(answer: str | None, facts: list[str]) -> tuple[bool, str]:
    """(ok, why not). An answer is acceptable only if it is made from the facts."""
    if not answer or not answer.strip():
        return False, "empty"
    if len(answer) > MAX_ANSWER_CHARS:
        return False, "too_long"
    if re.search(r"https?://|www\.", answer, re.IGNORECASE):
        return False, "contains_link"
    invented = ungrounded_numbers(answer, facts)
    if invented:
        return False, f"invented_numbers:{','.join(sorted(invented))}"
    return True, ""
