"""The life of an action: what it can become, when it is overdue, and how far it has got.

Pure rules, no database (services/actions.py applies them).

An action starts as `accepted` (or `pending`, if it was only proposed and needs approving) and moves
forward by people's choice: in progress, partly done, done, or cancelled. `overdue` is never chosen:
the system sets it when the target date passes while the work is still open, and clears it when the
date is moved on or the work moves forward. Done and cancelled are final.
"""

from dataclasses import dataclass
from datetime import date, timedelta

STATUSES = (
    "pending",
    "accepted",
    "in_progress",
    "partially_completed",
    "completed",
    "cancelled",
    "overdue",
)
FINAL = {"completed", "cancelled"}
# Work that is still going: it can become overdue, and it counts as open on the screen.
OPEN = {"accepted", "in_progress", "partially_completed", "overdue"}
DUE_SOON_DAYS = 7

# What a person may move an action to, from where it is. (Overdue is only ever set by the system.)
ALLOWED = {
    "pending": {"accepted", "cancelled"},
    "accepted": {"in_progress", "partially_completed", "completed", "cancelled"},
    "in_progress": {"partially_completed", "completed", "cancelled"},
    "partially_completed": {"in_progress", "completed", "cancelled"},
    "overdue": {"in_progress", "partially_completed", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
LABELS = {
    "pending": "Waiting for approval",
    "accepted": "Accepted",
    "in_progress": "In progress",
    "partially_completed": "Partly done",
    "completed": "Done",
    "cancelled": "Cancelled",
    "overdue": "Overdue",
}


def can_move(current: str, new: str) -> bool:
    return new in ALLOWED.get(current, set())


def next_statuses(current: str) -> list[str]:
    """What a person can move it to from here, in the order they usually would."""
    return [s for s in STATUSES if s in ALLOWED.get(current, set())]


def is_overdue(status: str, target_date: date | None, today: date) -> bool:
    """Still open, not already marked, and the target date is behind us."""
    return (
        status in OPEN and status != "overdue" and target_date is not None and target_date < today
    )


def is_back_on_time(status: str, target_date: date | None, today: date) -> bool:
    """Marked overdue, but the date has since been moved on (or removed)."""
    return status == "overdue" and (target_date is None or target_date >= today)


def due_soon(status: str, target_date: date | None, today: date) -> bool:
    """Open and due within the next few days (today counts, overdue does not)."""
    return (
        status in OPEN
        and status != "overdue"
        and target_date is not None
        and today <= target_date <= today + timedelta(days=DUE_SOON_DAYS)
    )


def days_late(target_date: date | None, today: date) -> int:
    return 0 if target_date is None else max(0, (today - target_date).days)


def dates_ok(start: date | None, target: date | None) -> bool:
    return start is None or target is None or target >= start


@dataclass
class Progress:
    done: int
    total: int

    @property
    def percent(self) -> int:
        return 0 if not self.total else round(self.done / self.total * 100)


def clean_steps(steps: list[dict | str]) -> list[dict]:
    """Steps as [{"text", "done"}], dropping blanks. Plain strings become undone steps."""
    out = []
    for step in steps:
        if isinstance(step, str):
            step = {"text": step, "done": False}
        text = str(step.get("text", "")).strip()
        if text:
            out.append({"text": text[:300], "done": bool(step.get("done", False))})
    return out


def progress(steps: list[dict]) -> Progress:
    return Progress(sum(1 for s in steps if s.get("done")), len(steps))


def default_target(start: date, days_to_effect: int) -> date:
    """When something is expected to show: a month's work is not due the day it starts."""
    return start + timedelta(days=max(1, days_to_effect))
