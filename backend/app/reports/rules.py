# ruff: noqa: E501
"""What a report is made of, when a scheduled one is next due, and how it is written out safely.

Pure rules, no database and no files (services/reports.py gathers the figures; render.py writes the PDF).

A report is stored as plain content: a title, a few facts about it, and sections that each hold some
sentences, a list, and/or one table. Everything else (the PDF, the CSV, the email) is made from that
one stored copy, so what is downloaded is always exactly what was written.
"""

import csv
import io
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

RULES_VERSION = "reports-1"
UK_TZ = ZoneInfo("Europe/London")
SEND_AT = time(7, 0)  # scheduled reports are written at 7am UK time
MAX_MONTHS = 24
LANDSCAPE_FROM = 7  # a table with this many columns or more is printed sideways
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

STANDARD = (
    ("monthly", "Monthly business report", 1),
    ("health", "Business health", 12),
    ("kpi", "Key figures", 6),
    ("outcomes", "What you tried and how it went", 12),
)
KIND_NAMES = {kind: name for kind, name, _ in STANDARD}


# --- the months a report covers ---------------------------------------------------------------------


def shift(month: date, by: int) -> date:
    index = month.year * 12 + month.month - 1 + by
    return date(index // 12, index % 12 + 1, 1)


def last_day(month: date) -> date:
    return shift(month, 1) - timedelta(days=1)


def months_window(latest: date, count: int) -> list[date]:
    """The `count` months ending with `latest`, oldest first."""
    return [shift(latest, -i) for i in range(count - 1, -1, -1)]


def period_label(first: date, last: date) -> str:
    if (first.year, first.month) == (last.year, last.month):
        return f"{first:%B %Y}"
    return f"{first:%B %Y} to {last:%B %Y}"


# --- scheduled delivery ------------------------------------------------------------------------------


def ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe(frequency: str, weekday: int | None, day: int | None) -> str:
    """The schedule in words."""
    if frequency == "weekly":
        return f"Every {WEEKDAYS[weekday - 1]} at 7am"
    return f"On the {ordinal(day)} of every month at 7am"


def next_run(frequency: str, weekday: int | None, day: int | None, after: datetime) -> datetime:
    """The next time (UTC) a schedule is due that is strictly later than `after`: 7am UK time on the
    chosen weekday, or on the chosen day of the month (always 1 to 28, so every month has one)."""
    local = after.astimezone(UK_TZ)
    candidate = local.date()
    for _ in range(62):
        at = datetime.combine(candidate, SEND_AT, tzinfo=UK_TZ)
        fits = candidate.isoweekday() == weekday if frequency == "weekly" else candidate.day == day
        if fits and at > local:
            return at.astimezone(UTC)
        candidate += timedelta(days=1)
    raise ValueError("no run found")  # unreachable for a valid schedule


# --- writing out safely ---------------------------------------------------------------------------------

_PLAIN_NUMBER = re.compile(r"^-[£$]?[\d,]+(\.\d+)?%?( points)?$")
_LATIN1 = {
    "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u25b2": "up", "\u25bc": "down", "\u2192": "to", "\u00a0": " ", "\u2022": "-",
}  # fmt: skip


def safe_cell(value: object) -> str:
    """A value for a spreadsheet cell. Text that a spreadsheet would run as a formula (it starts with
    =, +, @, a tab or a return, or a minus that is not a plain negative number) gets a quote in front,
    so a product or action name typed by a person cannot become a formula."""
    text = "" if value is None else str(value)
    if text == "-":
        return text  # an empty cell
    if text[:1] in ("=", "+", "@", "\t", "\r") or (
        text[:1] == "-" and not _PLAIN_NUMBER.match(text)
    ):
        return "'" + text
    return text


def latin1(text: str) -> str:
    """Text the PDF's built-in fonts can write: common punctuation is swapped for plain equivalents
    and anything else outside Western European letters becomes a question mark."""
    for old, new in _LATIN1.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def to_csv(content: dict) -> str:
    """The report as a CSV (with a byte-order mark so Excel reads the pound signs correctly)."""
    out = io.StringIO()
    out.write("\ufeff")
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([safe_cell(content["title"])])
    for fact in content.get("facts", []):
        w.writerow([safe_cell(fact)])
    for section in content["sections"]:
        w.writerow([])
        w.writerow([safe_cell(section["heading"])])
        for line in section.get("paragraphs", []):
            w.writerow([safe_cell(line)])
        for line in section.get("bullets", []):
            w.writerow(["- " + safe_cell(line)])
        table = section.get("table")
        if table:
            w.writerow([safe_cell(c) for c in table["columns"]])
            for row in table["rows"]:
                w.writerow([safe_cell(c) for c in row])
    for note in content.get("notes", []):
        w.writerow([])
        w.writerow([safe_cell(note)])
    return out.getvalue()


def filename(kind: str, content: dict, extension: str) -> str:
    return f"vyterlix-{kind}-{content['period_end'][:7]}.{extension}"


def landscape(content: dict) -> bool:
    return any(
        len(s["table"]["columns"]) >= LANDSCAPE_FROM for s in content["sections"] if s.get("table")
    )


def section(heading, *, paragraphs=None, bullets=None, columns=None, rows=None) -> dict:
    """One section of a report, in the form it is stored."""
    table = None
    if columns:
        table = {"columns": list(columns), "rows": [[str(c) for c in r] for r in rows]}
    return {
        "heading": heading,
        "paragraphs": list(paragraphs or []),
        "bullets": list(bullets or []),
        "table": table,
    }
