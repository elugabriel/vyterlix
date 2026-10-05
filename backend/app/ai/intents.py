# ruff: noqa: E501
"""Understanding what was asked, by fixed rules (no language model).

A question becomes an intent (what kind of answer is wanted), a figure (which KPI) and a month, using
the words in it and, for a follow-up like "and why?", what the conversation was just about. A question
the rules do not understand stays unknown: it is never handed to a model to guess an answer for.
"""

import re
from dataclasses import dataclass
from datetime import date

INTENTS = (
    "health", "kpi_value", "trend", "why", "forecast", "recommend", "actions", "outcomes",
    "normal", "greeting", "help", "unknown",
)  # fmt: skip

# The words people use for each figure. The longest phrase found wins, so "sales growth" is not "sales".
FIGURE_WORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("sales", "revenue", "turnover", "takings", "income"),
    "gross_sales": ("takings including vat", "sales including vat", "gross sales"),
    "sales_count": ("number of sales", "how many sales", "transactions", "orders"),
    "average_order_value": ("average sale", "average order", "average basket", "basket size"),
    "units_sold": ("items sold", "units sold", "how many items"),
    "refund_rate_pct": ("refund rate", "refunds", "returns"),
    "revenue_growth_pct": ("sales growth", "growth", "growing"),
    "revenue_vs_last_year_pct": (
        "compared with last year",
        "versus last year",
        "vs last year",
        "year on year",
    ),
    "gross_profit": ("gross profit",),
    "gross_margin_pct": ("gross margin",),
    "net_profit": ("net profit", "profit", "profits", "earnings"),
    "net_margin_pct": ("profit margin", "net margin"),
    "operating_expenses": ("running costs", "expenses", "overheads", "costs"),
    "cost_of_goods_sold": ("cost of goods", "cost of sales", "cogs"),
    "stock_purchases": ("stock bought", "stock purchases"),
    "stock_units": ("items in stock", "stock levels"),
    "stock_value": ("value of stock", "stock value"),
    "stock_turnover": ("stock turnover",),
    "days_of_stock": ("days of stock",),
    "out_of_stock_pct": ("out of stock", "stock outs", "stockouts"),
    "active_customers": (
        "customers who bought",
        "active customers",
        "customers",
        "customer numbers",
    ),
    "new_customers": ("new customers",),
    "returning_customers": ("returning customers",),
    "repeat_customer_rate_pct": ("repeat customers", "repeat rate"),
    "customer_retention_pct": ("retention", "customers who came back", "customers come back"),
    "customer_churn_pct": ("churn", "customers who did not come back", "lost customers"),
    "average_customer_value": ("spend per customer", "customer value", "average customer"),
    "customers_identified_pct": ("sales linked to a customer",),
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

_RULES: list[tuple[str, re.Pattern]] = [
    (
        "greeting",
        re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|cheers)\b"),
    ),
    (
        "help",
        re.compile(r"\b(help|what can you do|what do you do|what can i ask|how do you work)\b"),
    ),
    (
        "outcomes",
        re.compile(
            r"\b(did (it|that|this) (work|help)|did (it|that) make a difference|outcomes?|results? of|worked)\b"
        ),
    ),
    (
        "actions",
        re.compile(
            r"\b(my actions?|to.?do|overdue|what am i working on|what('s| is) (due|outstanding)|tasks?|in progress)\b"
        ),
    ),
    (
        "recommend",
        re.compile(
            r"\b(what should i do|what can i do|what do i do|how (can|do|should|could) i (fix|improve|increase|grow|boost|get|raise|lift|reduce|cut)|suggest|recommend|advice|ideas?|next step)\b"
        ),
    ),
    (
        "why",
        re.compile(
            r"\b(why|what caused|what('s| is) behind|reason|explain|how come|what happened)\b"
        ),
    ),
    (
        "forecast",
        re.compile(
            r"\b(forecast|predict|projection|next (month|quarter|few months)|coming months|will (my|i|we|it)|going to be|expect)\b"
        ),
    ),
    ("normal", re.compile(r"\b(normal|usual|typical|ordinary|unusual)\b")),
    (
        "trend",
        re.compile(
            r"\b(trend|over time|going (up|down)|history|last (few|\d+) months|rising|falling|improving|getting (better|worse))\b"
        ),
    ),
    (
        "health",
        re.compile(
            r"\b(health|how am i doing|how are we doing|how is (my|the|our) (business|company|shop)|business score|overall)\b"
        ),
    ),
]
_FOLLOW_UP = re.compile(r"^(and|what about|how about|and what about|what of)\b")
_ISO = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])\b")
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s+(20\d{2}))?"
)


@dataclass
class Understood:
    intent: str
    kpi_code: str | None = None
    month: date | None = None
    from_context: bool = False  # a figure or month was taken from the conversation so far


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s£%./-]", " ", text.lower().replace("'", ""))).strip()


def find_figure(text: str, extra_words: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """The figure the words point at: the code whose longest phrase appears in the text."""
    words = {
        code: (*phrases, *(extra_words or {}).get(code, ()))
        for code, phrases in FIGURE_WORDS.items()
    }
    words.update({c: p for c, p in (extra_words or {}).items() if c not in words})
    best: tuple[int, str] | None = None
    for code, phrases in words.items():
        for phrase in phrases:
            if re.search(r"\b" + re.escape(phrase) + r"\b", text) and (
                best is None or len(phrase) > best[0]
            ):
                best = (len(phrase), code)
    return None if best is None else best[1]


def _shift(month: date, by: int) -> date:
    index = month.year * 12 + month.month - 1 + by
    return date(index // 12, index % 12 + 1, 1)


def find_month(text: str, latest: date | None) -> date | None:
    """The month meant, given the latest finished month there are figures for."""
    if latest is None:
        return None
    iso = _ISO.search(text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), 1)
    if re.search(
        r"\b(last month|previous month|most recent month|latest month|this month)\b", text
    ):
        return latest
    if re.search(r"\b(the month before|month before that|two months ago)\b", text):
        return _shift(latest, -1)
    named = _MONTH_YEAR.search(text)
    if named:
        number = MONTHS[named.group(1)]
        if named.group(2):
            return date(int(named.group(2)), number, 1)
        year = (
            latest.year if number <= latest.month else latest.year - 1
        )  # the most recent one not in the future
        return date(year, number, 1)
    return None


def understand(
    question: str,
    *,
    latest: date | None,
    context: dict | None = None,
    extra_words: dict[str, tuple[str, ...]] | None = None,
) -> Understood:
    """Work out what is being asked. `context` is what the conversation was last about."""
    context = context or {}
    text = _clean(question)
    if not text:
        return Understood("unknown")
    intent = "unknown"
    for name, pattern in _RULES:
        if pattern.search(text):
            intent = name
            break
    kpi = find_figure(text, extra_words)
    month = find_month(text, latest)
    if intent == "unknown" and kpi is not None:
        intent = "kpi_value"
    if intent == "unknown" and _FOLLOW_UP.search(text) and (kpi or month):
        intent = context.get("intent") if context.get("intent") in INTENTS else "kpi_value"
    understood = Understood(intent, kpi, month)
    needs_figure = ("kpi_value", "trend", "why", "forecast", "normal")
    if (
        understood.kpi_code is None
        and context.get("kpi_code")
        and (intent in needs_figure or (intent == "recommend" and not month))
    ):
        understood.kpi_code, understood.from_context = context["kpi_code"], True
    if (
        understood.month is None
        and intent in ("kpi_value", "why")
        and context.get("month")
        and understood.from_context
    ):
        understood.month = date.fromisoformat(context["month"])
    return understood
