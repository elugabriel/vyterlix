# ruff: noqa: E501
"""Which things are worth an alert, how serious each is, and who is told and when.

Pure rules, no database (services/alerts.py and services/notifications.py apply them).

An alert is raised when something needs attention: a figure moved a lot, a forecast says a fall is
coming, work is overdue, data has gone stale or poor, or something happened to an account. Each kind
has defaults the owner can change (on or off, how serious, the threshold), except security alerts,
which cannot be turned off. The same thing is never raised twice while it is still open: a repeat is
counted on the alert already there.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

UK_TZ = ZoneInfo("Europe/London")
SEVERITIES = ("info", "low", "medium", "high", "critical")
RANK = {name: i for i, name in enumerate(SEVERITIES)}
EMAIL_FROM = "medium"  # lower severities appear in the app only
DIGEST_AFTER = 2  # this many or more emails to one person in one round become a single summary
SECURITY = "security"


@dataclass(frozen=True)
class RuleDef:
    code: str
    name: str
    category: str  # one of the nine alert areas
    severity: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    params_help: dict[str, str] = field(default_factory=dict)
    always_on: bool = False  # security alerts cannot be switched off


CHANGE_PARAMS = {"min_size": "major"}
CHANGE_HELP = {"min_size": "How big a change has to be: notable or major"}
RULES: tuple[RuleDef, ...] = (
    RuleDef(
        "change_sales",
        "A sales figure changed a lot",
        "sales",
        "high",
        "Sales figures such as sales, number of sales, items sold or the average sale moved against you by more than usual.",
        CHANGE_PARAMS,
        CHANGE_HELP,
    ),
    RuleDef(
        "change_financial",
        "A money figure changed a lot",
        "financial",
        "high",
        "Profit, margins or costs moved against you by more than usual.",
        CHANGE_PARAMS,
        CHANGE_HELP,
    ),
    RuleDef(
        "change_customer",
        "A customer figure changed a lot",
        "customer",
        "high",
        "The number of customers, how many come back or what they spend moved against you by more than usual.",
        CHANGE_PARAMS,
        CHANGE_HELP,
    ),
    RuleDef(
        "change_inventory",
        "A stock figure changed a lot",
        "inventory",
        "high",
        "Stock levels, days of stock or products out of stock moved against you by more than usual.",
        CHANGE_PARAMS,
        CHANGE_HELP,
    ),
    RuleDef(
        "change_marketing",
        "A marketing figure changed a lot",
        "marketing",
        "high",
        "A marketing figure moved against you by more than usual.",
        CHANGE_PARAMS,
        CHANGE_HELP,
    ),
    RuleDef(
        "forecast_decline",
        "The forecast says sales will fall",
        "forecast",
        "medium",
        "The forecast for next month's sales is lower than the latest month by more than the amount you choose.",
        {"drop_pct": 10},
        {"drop_pct": "How far the forecast has to be below the latest month, in per cent"},
    ),
    RuleDef(
        "action_overdue",
        "An action is overdue",
        "action",
        "medium",
        "Work you took up has gone past its finish date.",
        {"days": 1},
        {"days": "How many days late before you are told"},
    ),
    RuleDef(
        "data_stale",
        "No new sales data",
        "data",
        "medium",
        "The most recent sale you have is older than the number of days you choose.",
        {"days": 14},
        {"days": "Days without a new sale before you are told"},
    ),
    RuleDef(
        "data_quality",
        "Data is incomplete",
        "data",
        "medium",
        "The data behind the latest month is less complete than the score you choose.",
        {"min_score": 60},
        {"min_score": "Lowest data quality score (out of 100) you are happy with"},
    ),
    RuleDef(
        "health_drop",
        "Business health fell",
        "financial",
        "high",
        "Your business health score fell by more than the number of points you choose.",
        {"points": 10},
        {"points": "How many points the score has to fall"},
    ),
    RuleDef(
        "security_password_changed",
        "Your password was changed",
        SECURITY,
        "high",
        "The password on an account was changed or reset.",
        always_on=True,
    ),
)
BY_CODE = {r.code: r for r in RULES}
CHANGE_RULE_FOR = {
    "sales": "change_sales", "financial": "change_financial", "customer": "change_customer",
    "inventory": "change_inventory", "marketing": "change_marketing",
}  # fmt: skip


def lower(severity: str, steps: int = 1) -> str:
    """One step less serious (never below info)."""
    return SEVERITIES[max(0, RANK[severity] - steps)]


def at_least(severity: str, floor: str) -> bool:
    return RANK[severity] >= RANK[floor]


def change_severity(rule_severity: str, size: str, min_size: str) -> str | None:
    """How serious a change in a figure is: a major change is the rule's severity, a notable one a
    step lower. A change smaller than the owner asked about is not an alert at all."""
    if min_size == "major" and size != "major":
        return None
    return rule_severity if size == "major" else lower(rule_severity)


def settings_for(definition: RuleDef, row: Any | None) -> tuple[bool, str, dict]:
    """(on, severity, thresholds): the business's choice, or the defaults. Always-on rules ignore a
    choice to turn them off."""
    if row is None:
        return True, definition.severity, dict(definition.params)
    enabled = True if definition.always_on else row.enabled
    return enabled, row.severity, {**definition.params, **row.params}


# --- what makes the same alert --------------------------------------------------------------------


def key_change(code: str, period: date) -> str:
    return f"change:{code}:{period.isoformat()}"


def key_action(action_id) -> str:
    return f"action:{action_id}"


def key_forecast(code: str) -> str:
    return f"forecast:{code}"


def key_health(period: date) -> str:
    return f"health:{period.isoformat()}"


KEY_STALE = "data:stale"


def key_quality(period: date) -> str:
    return f"data:quality:{period.isoformat()}"


# --- who is told -----------------------------------------------------------------------------------


def in_audience(role: str, remit: list[str] | None, kpi_category: str | None) -> bool:
    """Owners are told everything. A Manager is told what is in their own area (or what belongs to
    no area). A Viewer cannot act on an alert, so is not sent them."""
    if role == "owner":
        return True
    if role != "manager":
        return False
    return kpi_category is None or remit is None or kpi_category in remit


# --- quiet hours -----------------------------------------------------------------------------------


def in_quiet_hours(now: datetime, start: time | None, end: time | None) -> bool:
    """Whether UK local time is inside quiet hours, which may cross midnight."""
    if start is None or end is None or start == end:
        return False
    local = now.astimezone(UK_TZ).time()
    return start <= local < end if start < end else local >= start or local < end


def quiet_ends(now: datetime, start: time | None, end: time | None) -> datetime:
    """When the current quiet hours finish (UTC)."""
    local = now.astimezone(UK_TZ)
    finish = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if finish <= local:
        finish += timedelta(days=1)
    return finish.astimezone(UTC)


def email_decision(
    *,
    category: str,
    severity: str,
    wants_email: bool,
    now: datetime,
    quiet: tuple[time, time] | None,
) -> tuple[str, datetime | None]:
    """("send" | "wait" | "none", when to send if waiting). Security alerts always go at once; other
    alerts of at least medium severity go by email if the person wants email for that area, and wait
    out quiet hours; critical alerts ignore quiet hours too."""
    if category == SECURITY:
        return "send", None
    if not wants_email or not at_least(severity, EMAIL_FROM):
        return "none", None
    if severity != "critical" and quiet and in_quiet_hours(now, quiet[0], quiet[1]):
        return "wait", quiet_ends(now, quiet[0], quiet[1])
    return "send", None
