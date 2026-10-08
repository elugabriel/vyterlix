# ruff: noqa: E501
"""What goes on the front screen, and in what order.

Pure rules, no database (services/dashboard.py gathers the facts and applies them).

The screen opens with what needs attention today, most serious first, and only after that shows how
the business is doing. Each thing is for the people who can do something about it: an owner sees
everything; a Manager sees what is in their own area (and what belongs to no area); a Viewer sees what
is going on but is given nothing to do.
"""

from dataclasses import dataclass, field

from app.alerts.rules import RANK

MAX_ITEMS = 12
KIND_ORDER = ("approval", "action_overdue", "alert", "follow_up", "action_due_soon", "suggestion")
HEADLINE_FIGURES = (
    "revenue",
    "gross_profit",
    "net_profit",
    "active_customers",
    "average_order_value",
)


@dataclass
class Item:
    kind: str
    severity: str
    title: str
    detail: str
    link: str
    category: str | None = None  # the area, for who may act on it
    id: str = ""
    data: dict = field(default_factory=dict)


def order(items: list[Item]) -> list[Item]:
    """Most serious first; within the same seriousness, the kinds that need a person first (an
    approval is waiting on someone), then overdue work, alerts, follow-ups, work due soon, and
    suggestions; then a stable order by what it is."""
    return sorted(items, key=lambda i: (-RANK[i.severity], KIND_ORDER.index(i.kind), i.title))


def visible(role: str, remit: list[str] | None, category: str | None) -> bool:
    """Whether this person is shown a thing of that area. Owners and Viewers see everything (a Viewer
    can only look); a Manager sees their own area and whatever belongs to none."""
    if role != "manager":
        return True
    return category is None or remit is None or category in remit


def can_act(role: str, remit: list[str] | None, category: str | None) -> bool:
    """Whether they are given something to do about it."""
    return role in ("owner", "manager") and visible(role, remit, category)


def top(items: list[Item]) -> tuple[list[Item], int]:
    """The ones to show, and how many more there are."""
    ordered = order(items)
    return ordered[:MAX_ITEMS], max(0, len(ordered) - MAX_ITEMS)


def greeting_line(count: int, serious: int) -> str:
    """The one sentence that opens the screen."""
    if count == 0:
        return "Nothing needs your attention today."
    things = "thing needs" if count == 1 else "things need"
    if serious:
        return f"{count} {things} your attention today, {serious} of them serious."
    return f"{count} {things} your attention today."
