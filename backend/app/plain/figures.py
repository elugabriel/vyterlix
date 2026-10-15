# ruff: noqa: E501
"""Saying what a figure means, in everyday words (client feedback 15/10/2026, section 2).

A small business owner who has never studied data should not have to guess whether "stock turnover
up 16%" is good news. For every key figure this says, in one short sentence, what happened, and in
a second sentence why it matters, then the screen shows the number underneath.

The words are rules written as data, like the health and alert rules: one entry per figure with what
to say when it rises and when it falls, so a change in wording is a change to this table.

Two promises are kept by the tests:

- It never states a cause. It says what a change *means* ("less money is tied up in stock"), never
  *why it happened* ("because of your promotion"), because the figure alone cannot know that. The
  causes Vyterlix can back with evidence live on the What changed page.
- It says so when it cannot tell: with no earlier period to compare, or when the figure's own
  worth depends on the business ("items in stock" is neither good nor bad by itself).

No database access: a figure goes in, words come out.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Tone = Literal["good", "bad", "watch", "neutral", "unknown"]

# Smaller than this is "about the same": 1% for money, counts and ratios, half a point for a percentage.
FLAT_PCT = Decimal("1")
FLAT_POINTS = Decimal("0.5")
# Below this share of complete data behind a figure, say so.
LOW_QUALITY = 70

# What "the period before" is called for each way of grouping the figures.
BEFORE = {
    "week": "last week",
    "month": "last month",
    "quarter": "last quarter",
    "year": "last year",
}
THIS = {
    "week": "this week",
    "month": "this month",
    "quarter": "this quarter",
    "year": "this year",
}


@dataclass(frozen=True)
class Say:
    """What to say for one direction of change."""

    says: str  # what happened, one sentence
    why: str  # why it matters, one or two sentences
    tone: Tone | None = None  # leave out to work it out from the figure's good direction


@dataclass(frozen=True)
class Words:
    up: Say
    down: Say


# "{before}" stands for the period before ("last month"). Nothing in here claims a cause.
WORDS: dict[str, Words] = {
    # --- customers ---------------------------------------------------------------------
    "active_customers": Words(
        Say(
            "More different customers bought from you than {before}.",
            "A wider group of customers usually means steadier sales.",
        ),
        Say(
            "Fewer different customers bought from you than {before}.",
            "Fewer buyers can mean lower sales next month, unless those who stay spend more.",
        ),
    ),
    "new_customers": Words(
        Say(
            "More people bought from you for the first time than {before}.",
            "New customers are how a business grows. It is worth seeing whether they come back.",
        ),
        Say(
            "Fewer people bought from you for the first time than {before}.",
            "Fewer new customers can slow your growth.",
        ),
    ),
    "returning_customers": Words(
        Say(
            "More of your past customers came back to buy again than {before}.",
            "Customers who return are usually cheaper to keep than new ones are to win.",
        ),
        Say(
            "Fewer of your past customers came back to buy again than {before}.",
            "Returning customers are your steadiest source of sales, so a drop is worth watching.",
        ),
    ),
    "repeat_customer_rate_pct": Words(
        Say(
            "A bigger share of your buyers have bought from you before.",
            "That is a good sign that people like coming back.",
        ),
        Say(
            "A smaller share of your buyers have bought from you before.",
            "You may be relying more on new customers, which is harder work than keeping the ones you have.",
        ),
    ),
    "customer_retention_pct": Words(
        Say(
            "More of {before}'s customers bought from you again.",
            "That is a good sign that people like what you offer.",
        ),
        Say(
            "Fewer of {before}'s customers bought from you again.",
            "This could reduce next month's sales. It is worth reconnecting with the customers who did not come back.",
        ),
    ),
    "customer_churn_pct": Words(
        Say(
            "More customers stopped coming back than {before}.",
            "This could reduce next month's sales. It is worth finding out which customers are affected and reconnecting with them.",
        ),
        Say(
            "Fewer customers stopped coming back than {before}.",
            "That is a good sign: more of your customers are staying with you.",
        ),
    ),
    "average_customer_value": Words(
        Say(
            "Each customer spent more with you, on average, than {before}.",
            "Spending more per customer lifts your sales without needing more customers.",
        ),
        Say(
            "Each customer spent less with you, on average, than {before}.",
            "Your sales can fall even when the same number of customers buy.",
        ),
    ),
    "customers_identified_pct": Words(
        Say(
            "More of your sales now say who bought.",
            "The more sales that say who bought, the more you can trust the customer figures.",
            "good",
        ),
        Say(
            "Fewer of your sales now say who bought.",
            "The customer figures, such as repeat customers, are less reliable when sales do not record the customer.",
            "watch",
        ),
    ),
    # --- money --------------------------------------------------------------------------
    "revenue": Words(
        Say(
            "You sold more than {before}.",
            "More sales is good news, as long as your costs did not rise just as fast.",
        ),
        Say(
            "You sold less than {before}.",
            "Check whether this is a normal quiet spell for your business before reacting.",
        ),
    ),
    "gross_sales": Words(
        Say(
            "Customers paid you more in total than {before}, VAT included.",
            "VAT is not yours to keep, so the Sales figure is the better guide to how you are doing.",
            "neutral",
        ),
        Say(
            "Customers paid you less in total than {before}, VAT included.",
            "VAT is not yours to keep, so the Sales figure is the better guide to how you are doing.",
            "neutral",
        ),
    ),
    "cost_of_goods_sold": Words(
        Say(
            "The goods you sold cost you more than {before}.",
            "That is normal if you sold more. If your sales did not rise too, you may be earning less on each sale.",
            "watch",
        ),
        Say(
            "The goods you sold cost you less than {before}.",
            "That is good if your sales held up. If you simply sold less, it is not a gain.",
            "watch",
        ),
    ),
    "gross_profit": Words(
        Say(
            "You kept more from your sales, after the cost of the goods, than {before}.",
            "This is the money left to pay your running costs.",
        ),
        Say(
            "You kept less from your sales, after the cost of the goods, than {before}.",
            "There is less left to pay your running costs.",
        ),
    ),
    "gross_margin_pct": Words(
        Say(
            "A bigger share of each sale is left after the cost of the goods.",
            "Each sale is earning you more.",
        ),
        Say(
            "A smaller share of each sale is left after the cost of the goods.",
            "Your prices, your discounts and what you pay your suppliers are worth a look.",
        ),
    ),
    "operating_expenses": Words(
        Say(
            "Your running costs are higher than {before}.",
            "If your sales did not rise as much, your profit will be squeezed. The biggest increases are the place to start.",
        ),
        Say(
            "Your running costs are lower than {before}.",
            "That helps your profit, as long as it did not come at the cost of your service.",
        ),
    ),
    "net_profit": Words(
        Say(
            "Your business made more profit than {before}.",
            "Profit is what you can keep or put back into the business, so this is a good sign.",
        ),
        Say(
            "Your business made less profit than {before}.",
            "Look at whether your sales fell, your costs rose, or both.",
        ),
    ),
    "net_margin_pct": Words(
        Say(
            "A bigger share of every pound you sell ends up as profit.",
            "You are keeping more for the work you do.",
        ),
        Say(
            "A smaller share of every pound you sell ends up as profit.",
            "You may be working just as hard for less.",
        ),
    ),
    "stock_purchases": Words(
        Say(
            "You spent more on buying stock than {before}.",
            "That is fine if you expect to sell more. Otherwise money may get tied up in stock.",
            "watch",
        ),
        Say(
            "You spent less on buying stock than {before}.",
            "That keeps more cash in the business, but check that your popular items do not run out.",
            "watch",
        ),
    ),
    # --- stock --------------------------------------------------------------------------
    "stock_units": Words(
        Say(
            "You are holding more items in stock than {before}.",
            "Check they are the items people buy, so cash is not tied up in things that do not sell.",
            "watch",
        ),
        Say(
            "You are holding fewer items in stock than {before}.",
            "That frees up cash, but make sure your popular items do not run out.",
            "watch",
        ),
    ),
    "stock_value": Words(
        Say(
            "More of your money is sitting in stock than {before}.",
            "It is worth checking that the stock is selling.",
            "watch",
        ),
        Say(
            "Less of your money is sitting in stock than {before}.",
            "That helps your cash, but check you can still serve your customers.",
            "watch",
        ),
    ),
    "stock_turnover": Words(
        Say(
            "Your products are selling faster than {before}.",
            "That is usually positive because less money is sitting in unsold stock. Check that your popular products do not run out.",
        ),
        Say(
            "Your products are selling more slowly than {before}.",
            "Money may be tied up in stock that is not moving. Look at which items are slow.",
        ),
    ),
    "days_of_stock": Words(
        Say(
            "Your stock would now last longer, at the rate you are selling, than it did {before}.",
            "That is comfortable, but a very long time can mean money is tied up.",
            "watch",
        ),
        Say(
            "Your stock would now run out sooner, at the rate you are selling, than it did {before}.",
            "Check that you can reorder in time for your popular items.",
            "watch",
        ),
    ),
    "out_of_stock_pct": Words(
        Say(
            "More of your products have run out than {before}.",
            "Customers who cannot buy what they want may go elsewhere.",
        ),
        Say("Fewer of your products have run out than {before}.", "That helps you keep selling."),
    ),
    # --- sales --------------------------------------------------------------------------
    "sales_count": Words(
        Say(
            "You made more separate sales than {before}.",
            "More sales is good news. It is worth seeing whether each sale is also worth as much.",
        ),
        Say(
            "You made fewer separate sales than {before}.",
            "Fewer sales can mean fewer customers coming through the door or ordering.",
        ),
    ),
    "average_order_value": Words(
        Say(
            "Each sale was worth more, on average, than {before}.",
            "That lifts your takings without needing more customers.",
        ),
        Say(
            "Each sale was worth less, on average, than {before}.",
            "Your sales can fall even when the number of sales holds up.",
        ),
    ),
    "units_sold": Words(
        Say(
            "You sold more items than {before}.",
            "Selling more items is good news, as long as the price per item held up.",
        ),
        Say(
            "You sold fewer items than {before}.",
            "Check whether this is a normal quiet spell for your business.",
        ),
    ),
    "refund_rate_pct": Words(
        Say(
            "More of your sales were refunded than {before}.",
            "A refund costs you the sale, and often the goods too. It is worth finding which products or sales they come from.",
        ),
        Say(
            "Fewer of your sales were refunded than {before}.",
            "That is a good sign that customers are happy with what they bought.",
        ),
    ),
    # These two figures *are* a comparison, so what is said depends on whether the figure itself is
    # above or below zero (see VALUE_FIGURES), not on how it moved.
    "revenue_growth_pct": Words(
        Say(
            "Your sales grew compared with {before}.",
            "Growth is a good sign. Keep an eye on whether it carries on.",
        ),
        Say(
            "Your sales shrank compared with {before}.",
            "Check whether this is a normal quiet spell for your business.",
        ),
    ),
    "revenue_vs_last_year_pct": Words(
        Say(
            "You are selling more than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons.",
        ),
        Say(
            "You are selling less than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons, so this is worth a closer look.",
        ),
    ),
}

# When a profit figure is below zero the business is losing money, which "less profit than last
# month" does not say. Said whichever way the figure moved.
LOSS: dict[str, Say] = {
    "gross_profit": Say(
        "The goods you sold cost you more than you sold them for {this}.",
        "Every sale is losing money before your running costs. Your prices and what you pay suppliers need a look.",
    ),
    "net_profit": Say(
        "Your business made a loss {this}.",
        "Your costs were higher than your sales. The biggest costs are the place to start.",
        "bad",
    ),
    "net_margin_pct": Say(
        "You spent more than you took in {this}.",
        "Your costs were higher than your sales. The biggest costs are the place to start.",
        "bad",
    ),
}

# Figures that are already a comparison: judge them by whether the figure is above or below zero.
VALUE_FIGURES = frozenset({"revenue_growth_pct", "revenue_vs_last_year_pct"})


@dataclass(frozen=True)
class Plain:
    says: str
    why: str
    tone: Tone
    caution: str | None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except ArithmeticError:
        return None


def _tone(say: Say, direction: str, going_up: bool) -> Tone:
    if say.tone is not None:
        return say.tone
    if direction == "up_good":
        return "good" if going_up else "bad"
    if direction == "down_good":
        return "bad" if going_up else "good"
    return "neutral"


def explain_figure(
    code: str,
    *,
    unit: str,
    direction: str,
    value: str | None,
    change_pct: str | None,
    previous_value: str | None = None,
    data_quality: int | None = None,
    granularity: str = "month",
) -> Plain | None:
    """The words for one figure in one period, or None when there is no number to talk about.

    `change_pct` is how far the figure moved since the period before: a percentage of itself for
    pounds, counts and ratios, and a number of points for a figure that is itself a percentage.
    """
    words = WORDS.get(code)
    number = _decimal(value)
    if words is None or number is None:
        return None
    before = BEFORE.get(granularity, "the period before")
    caution = None
    if data_quality is not None and data_quality < LOW_QUALITY:
        caution = "Some of the data behind this figure is incomplete, so treat it with care."

    if code in VALUE_FIGURES:
        if number == 0:
            return Plain(
                f"Your sales are the same as {before}."
                if code == "revenue_growth_pct"
                else "Your sales are about the same as at this time last year.",
                "Nothing to act on here.",
                "neutral",
                caution,
            )
        say = words.up if number > 0 else words.down
        return Plain(
            say.says.format(before=before), say.why, _tone(say, direction, number > 0), caution
        )

    if number < 0 and code in LOSS:
        say = LOSS[code]
        return Plain(
            say.says.format(this=THIS.get(granularity, "in this period")),
            say.why,
            _tone(say, direction, False),
            caution,
        )

    change = _decimal(change_pct)
    previous = _decimal(previous_value)
    if change is None and previous is not None:
        # The earlier period is there but a percentage cannot be worked out (it was nothing).
        # Judge by which way the figure itself moved.
        moved = number - previous
        change = Decimal(0) if moved == 0 else Decimal(100) if moved > 0 else Decimal(-100)
    if change is None:
        return Plain(
            "This is the first period we can show, so there is nothing to compare it with yet.",
            "We need one more period before we can say whether this is good or bad.",
            "unknown",
            caution,
        )
    flat = FLAT_POINTS if unit == "percent" else FLAT_PCT
    if abs(change) < flat:
        return Plain(
            f"This is about the same as {before}.",
            "Nothing to act on here.",
            "neutral",
            caution,
        )
    say = words.up if change > 0 else words.down
    return Plain(
        say.says.format(before=before),
        say.why.format(before=before),
        _tone(say, direction, change > 0),
        caution,
    )
