# ruff: noqa: E501
"""Putting an answer together from what was looked up.

The plain answer is built here from the facts the tools returned and nothing else, so it can be
traced line by line. An outside language model may be allowed to reword it (see provider.py), but
only the facts, never anything it knows itself, and what it writes is checked against them.
"""

from dataclasses import dataclass, field

CAN_HELP_WITH = (
    "how your business is doing overall",
    "any of your figures for a month (for example: What were my sales in March?)",
    "how a figure has moved over the last months",
    "why a figure changed",
    "what to expect over the next months",
    "what you could do about a fall",
    "your actions and how they are going",
    "whether the things you tried worked",
    "whether a figure is normal for you",
)

GREETING = "Hello. Ask me about your business figures, for example: How are we doing? or Why did sales fall?"
UNKNOWN = (
    "I can only answer from your business's own figures and records, and I could not tell what you "
    "are asking about. I can tell you about " + "; ".join(CAN_HELP_WITH) + "."
)
HELP = (
    "I answer from your own figures and records only, never from guesswork. You can ask me about: "
    + "; ".join(CAN_HELP_WITH)
    + "."
)
SWITCHED_OFF = "The assistant has been switched off for this business."


@dataclass
class ToolResult:
    """What one look-up found: facts in plain English (each with its figures already in them), where
    they came from, and what to remember for the next question."""

    tool: str
    ok: bool
    facts: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    note: str | None = None  # why there is nothing to report, when ok is False
    context: dict = field(default_factory=dict)
    arguments: dict = field(default_factory=dict)


LEADS = {
    "health": "Here is how your business is doing.",
    "trend": "Here is how it has moved.",
    "why": "Here is what we found.",
    "forecast": "Here is what to expect.",
    "recommend": "Here is what we suggest.",
    "actions": "Here is where your actions stand.",
    "outcomes": "Here is how the things you tried turned out.",
    "normal": "Here is what is usual for you.",
}


def plain_answer(intent: str, results: list[ToolResult]) -> str:
    """The answer made only from the facts. Where there were none, say why, and say nothing more."""
    facts = [fact for r in results if r.ok for fact in r.facts]
    if not facts:
        notes = [r.note for r in results if r.note]
        return notes[0] if notes else UNKNOWN
    lead = LEADS.get(intent)
    return "\n".join(([lead] if lead else []) + facts)


def all_facts(results: list[ToolResult]) -> list[str]:
    return [fact for r in results if r.ok for fact in r.facts]


def all_sources(results: list[ToolResult]) -> list[dict]:
    seen, out = set(), []
    for r in results:
        if not r.ok:
            continue
        for source in r.sources:
            key = (source.get("kind"), source.get("ref"))
            if key not in seen:
                seen.add(key)
                out.append(source)
    return out
