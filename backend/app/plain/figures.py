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
    "regular_clients": Words(
        Say(
            "More clients came three or more times in the last three months than {before}.",
            "Regulars are the steadiest part of your income. They are the ones to offer a standing booking or a regular arrangement.",
        ),
        Say(
            "Fewer clients came three or more times in the last three months than {before}.",
            "Fewer regulars means less steady income. It is worth getting in touch with the ones who have stopped coming.",
        ),
    ),
    "happy_ratings_pct": Words(
        Say(
            "A bigger share of your customers rated you 4 or 5 than {before}.",
            "That is a good sign: more customers are happy with what you did.",
        ),
        Say(
            "A smaller share of your customers rated you 4 or 5 than {before}.",
            "Happy customers come back and recommend you. It is worth finding out what changed.",
        ),
    ),
    "unhappy_ratings_pct": Words(
        Say(
            "A bigger share of your customers rated you 1 or 2 than {before}.",
            "An unhappy customer is more likely to leave. A personal call can often put it right.",
        ),
        Say(
            "A smaller share of your customers rated you 1 or 2 than {before}.",
            "That is a good sign: fewer customers are unhappy.",
        ),
    ),
    "bank_money_in": Words(
        Say(
            "More money was paid into your bank than {before}.",
            "More money coming in gives you more room to pay bills and grow.",
        ),
        Say(
            "Less money was paid into your bank than {before}.",
            "Check whether any customers are late paying, so you can chase them early.",
        ),
    ),
    "bank_money_out": Words(
        Say(
            "More money left your bank than {before}.",
            "That is not good or bad by itself. It is worth checking it was money you meant to spend.",
        ),
        Say(
            "Less money left your bank than {before}.",
            "That is not good or bad by itself. It is worth checking nothing that was due was missed.",
        ),
    ),
    "website_visits": Words(
        Say(
            "More people visited your website than {before}.",
            "More visits means more people seeing what you offer.",
        ),
        Say(
            "Fewer people visited your website than {before}.",
            "Fewer visits can mean fewer enquiries later. It is worth checking where your visitors usually come from.",
        ),
    ),
    "website_new_visitors": Words(
        Say(
            "More people visited your website for the first time than {before}.",
            "New visitors are people who did not know you before, so this is a sign you are being found.",
        ),
        Say(
            "Fewer people visited your website for the first time than {before}.",
            "Fewer new visitors means fewer new people finding you.",
        ),
    ),
    "website_actions": Words(
        Say(
            "More visitors sent an enquiry or bought than {before}.",
            "That is a good sign: your website is turning visits into business.",
        ),
        Say(
            "Fewer visitors sent an enquiry or bought than {before}.",
            "Check that your contact form and checkout still work, and that visitors can find them easily.",
        ),
    ),
    "new_leads": Words(
        Say(
            "More enquiries came in than {before}.",
            "More enquiries means more chances to win work.",
        ),
        Say(
            "Fewer enquiries came in than {before}.",
            "Fewer enquiries now can mean less work later. It is worth asking past clients who else they know.",
        ),
    ),
    "proposals_sent": Words(
        Say(
            "You sent more proposals than {before}.",
            "More proposals out means more chances to win work.",
        ),
        Say(
            "You sent fewer proposals than {before}.",
            "If enquiries are coming in, check that none are waiting for a reply.",
        ),
    ),
    "win_rate_pct": Words(
        Say(
            "You won a bigger share of the proposals that were decided than {before}.",
            "That is a good sign: more of your proposals are turning into work.",
        ),
        Say(
            "You won a smaller share of the proposals that were decided than {before}.",
            "It is worth asking a few clients you lost what made the difference.",
        ),
    ),
    "work_won": Words(
        Say(
            "You won more work than {before}.",
            "Work won now is income in the months ahead.",
        ),
        Say(
            "You won less work than {before}.",
            "Less work won now can mean a quieter few months ahead.",
        ),
    ),
    "renewal_rate_pct": Words(
        Say(
            "More clients renewed than {before}.",
            "That is a good sign: clients are staying.",
        ),
        Say(
            "Fewer clients renewed than {before}.",
            "A conversation before the renewal is due often saves the ones who were unsure.",
        ),
    ),
    "money_owed": Words(
        Say(
            "You are owed more than {before}.",
            "Money owed is money you have earned but not yet been paid. If it keeps growing, cash can get tight.",
        ),
        Say(
            "You are owed less than {before}.",
            "That is a good sign: customers are paying what they owe.",
        ),
    ),
    "overdue_invoices": Words(
        Say(
            "More money is overdue than {before}.",
            "Overdue invoices are the ones past their due day. A polite reminder often brings them in.",
        ),
        Say(
            "Less money is overdue than {before}.",
            "That is a good sign: fewer customers are late paying.",
        ),
    ),
    "days_to_get_paid": Words(
        Say(
            "Invoices took longer to be paid than {before}.",
            "The longer customers take, the longer your cash is tied up.",
        ),
        Say(
            "Invoices were paid faster than {before}.",
            "That is a good sign: your cash comes in sooner.",
        ),
    ),
    "appointments_kept": Words(
        Say(
            "More appointments went ahead than {before}.",
            "More appointments kept usually means more money coming in.",
        ),
        Say(
            "Fewer appointments went ahead than {before}.",
            "Fewer appointments can mean less money coming in, unless you charged more for each.",
        ),
    ),
    "cancellation_rate_pct": Words(
        Say(
            "A bigger share of appointments were cancelled than {before}.",
            "Cancelled slots are time you cannot sell again. A reminder the day before can help.",
        ),
        Say(
            "A smaller share of appointments were cancelled than {before}.",
            "That is a good sign: more of your booked time is being used.",
        ),
    ),
    "no_show_rate_pct": Words(
        Say(
            "A bigger share of customers did not turn up than {before}.",
            "A missed appointment is time you cannot get back. A reminder a day or two before often helps.",
        ),
        Say(
            "A smaller share of customers did not turn up than {before}.",
            "That is a good sign: more of your booked time is being used.",
        ),
    ),
    "late_cancellations": Words(
        Say(
            "More appointments were cancelled at the last minute than {before}.",
            "Late cancellations are hard to fill. A clear cancellation policy and a reminder can help.",
        ),
        Say(
            "Fewer appointments were cancelled at the last minute than {before}.",
            "That is a good sign: fewer gaps in your day.",
        ),
    ),
    "rebooking_rate_pct": Words(
        Say(
            "More customers have another appointment booked than {before}.",
            "That is a good sign that people want to come back.",
        ),
        Say(
            "Fewer customers have another appointment booked than {before}.",
            "Offering the next appointment before they leave can help. Recent months can look low as people have not booked yet.",
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
    "appointments_booked": Words(
        Say(
            "More appointments were booked than {before}.",
            "More booked time is good for the business. Check you have the people to deliver it.",
        ),
        Say(
            "Fewer appointments were booked than {before}.",
            "Quiet spells happen. A reminder to regular customers, or a small offer for the quiet days, can help fill the diary.",
        ),
    ),
    "completion_rate_pct": Words(
        Say(
            "A bigger share of appointments went ahead than {before}.",
            "That is a good sign: less time lost to cancellations and no-shows.",
        ),
        Say(
            "A smaller share of appointments went ahead than {before}.",
            "Cancellations and no-shows are taking up your time. A reminder a day or two before often helps.",
        ),
    ),
    "visits_per_customer": Words(
        Say(
            "Customers came in more often than {before}.",
            "That is a good sign of loyalty: regular visits are the most reliable income.",
        ),
        Say(
            "Customers came in less often than {before}.",
            "Offering the next appointment before they leave, or a reminder at their usual interval, can bring them back sooner.",
        ),
    ),
    "new_clients": Words(
        Say(
            "More new clients came in than {before}.",
            "That is good: new people are finding you. Make sure each is asked to book again.",
        ),
        Say(
            "Fewer new clients came in than {before}.",
            "Look at how people find you. A word from a happy customer is often the cheapest way to bring in new clients.",
        ),
    ),
    "average_appointment_minutes": Words(
        Say(
            "Appointments took longer on average than {before}.",
            "Longer appointments are fine if you charge for the time. If not, you may be doing more for the same money.",
        ),
        Say(
            "Appointments took less time on average than {before}.",
            "Shorter appointments free up time for more customers, as long as quality does not slip.",
        ),
    ),
    "overrun_pct": Words(
        Say(
            "More appointments ran over their planned time than {before}.",
            "Running over pushes the rest of the day back and keeps customers waiting. Check you are planning enough time.",
        ),
        Say(
            "Fewer appointments ran over their planned time than {before}.",
            "That is a good sign: your day is running closer to plan.",
        ),
    ),
    "hours_worked": Words(
        Say(
            "Your team worked more hours than {before}.",
            "More hours cost more. Check the extra time is earning its keep.",
        ),
        Say(
            "Your team worked fewer hours than {before}.",
            "Fewer hours cost less. Check you still have enough cover for your busy times.",
        ),
    ),
    "revenue_per_hour": Words(
        Say(
            "Your team brought in more for each hour worked than {before}.",
            "That is a good sign: the time is being used well.",
        ),
        Say(
            "Your team brought in less for each hour worked than {before}.",
            "Quiet hours still cost wages. Look at when the diary is empty, and whether your prices cover the time.",
        ),
    ),
    "utilisation_pct": Words(
        Say(
            "A bigger share of your team's time went on appointments than {before}.",
            "That is a good sign: less time spent waiting for customers.",
        ),
        Say(
            "A smaller share of your team's time went on appointments than {before}.",
            "Gaps in the diary are paid time with no income. Reminders and offers for quiet times can help fill them.",
        ),
    ),
    "appointments_per_hour": Words(
        Say(
            "Your team saw more customers for each hour worked than {before}.",
            "That is good, as long as nobody is being rushed.",
        ),
        Say(
            "Your team saw fewer customers for each hour worked than {before}.",
            "Look for gaps in the diary, or appointments taking longer than planned.",
        ),
    ),
    "ad_spend": Words(
        Say(
            "You spent more on adverts than {before}.",
            "Check the extra money is bringing more customers, not only more clicks.",
        ),
        Say(
            "You spent less on adverts than {before}.",
            "Spending less is fine if your enquiries and sales have held up.",
        ),
    ),
    "ad_clicks": Words(
        Say(
            "More people clicked on your adverts than {before}.",
            "More clicks only help if they turn into enquiries and sales.",
        ),
        Say(
            "Fewer people clicked on your adverts than {before}.",
            "Check whether you spent less. If not, the adverts may be getting tired, and a fresh picture or message can help.",
        ),
    ),
    "ad_cost_per_click": Words(
        Say(
            "Each click on your adverts cost more than {before}.",
            "Clicks are getting dearer. A clearer message or a tighter group of people to show it to can bring the price down.",
        ),
        Say(
            "Each click on your adverts cost less than {before}.",
            "That is a good sign: your money is going further.",
        ),
    ),
    "ad_results": Words(
        Say(
            "Your adverts brought more enquiries and orders than {before}.",
            "That is a good sign that they are reaching the right people.",
        ),
        Say(
            "Your adverts brought fewer enquiries and orders than {before}.",
            "Look at which adverts are working and which are not, and put your money behind the better ones.",
        ),
    ),
    "ad_cost_per_result": Words(
        Say(
            "Each enquiry or order from your adverts cost more than {before}.",
            "You are paying more for the same result. It is worth looking at which adverts cost the most.",
        ),
        Say(
            "Each enquiry or order from your adverts cost less than {before}.",
            "That is a good sign: your adverts are working harder for the same money.",
        ),
    ),
    "cost_per_new_customer": Words(
        Say(
            "Winning each new customer cost you more than {before}.",
            "You spent more on adverts for each new customer. Remember that some new customers find you without an advert.",
        ),
        Say(
            "Winning each new customer cost you less than {before}.",
            "That is a good sign: each pound on adverts is going further.",
        ),
    ),
    "sales_per_ad_pound": Words(
        Say(
            "You sold more for each £1 spent on adverts than {before}.",
            "That is a good sign. It counts all your sales, not only those that came from an advert.",
        ),
        Say(
            "You sold less for each £1 spent on adverts than {before}.",
            "Either you spent more or you sold less. It counts all your sales, not only those that came from an advert.",
        ),
    ),
    "supplier_bought": Words(
        Say(
            "You spent more with suppliers than {before}.",
            "Check the extra buying matches what you sold, so stock or supplies are not piling up.",
        ),
        Say(
            "You spent less with suppliers than {before}.",
            "That is fine if you are selling less or using up what you already hold.",
        ),
    ),
    "suppliers_used": Words(
        Say(
            "You bought from more suppliers than {before}.",
            "More suppliers can mean better choice, but it is more to keep track of.",
        ),
        Say(
            "You bought from fewer suppliers than {before}.",
            "Fewer suppliers is simpler, but make sure you are not relying on one of them too much.",
        ),
    ),
    "top_supplier_share_pct": Words(
        Say(
            "More of your buying went to your biggest supplier than {before}.",
            "If that one supplier let you down, you would feel it. It is worth knowing who else could supply you.",
        ),
        Say(
            "Less of your buying went to your biggest supplier than {before}.",
            "Spreading your buying is safer. Keep an eye on the prices you are getting.",
        ),
    ),
    "supplier_credit_rate_pct": Words(
        Say(
            "Suppliers gave you back more money for problem goods than {before}.",
            "Credit notes usually mean something arrived wrong or was sent back. Look at which supplier it was.",
        ),
        Say(
            "Suppliers gave you back less money for problem goods than {before}.",
            "That is a good sign: fewer things are coming back.",
        ),
    ),
    "sales_count_growth_pct": Words(
        Say(
            "You made more sales than {before}.",
            "More sales means more people are buying. Check you can serve them all.",
        ),
        Say(
            "You made fewer sales than {before}.",
            "Fewer people bought. Check whether this is a normal quiet spell for your business.",
        ),
    ),
    "sales_count_vs_last_year_pct": Words(
        Say(
            "You made more sales than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons.",
        ),
        Say(
            "You made fewer sales than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons, so this is worth a closer look.",
        ),
    ),
    "units_growth_pct": Words(
        Say(
            "You sold more items than {before}.",
            "More items going out means demand is up. Check you have enough to cover it.",
        ),
        Say(
            "You sold fewer items than {before}.",
            "Less is going out. Check whether this is a normal quiet spell before you reorder.",
        ),
    ),
    "bookings_growth_pct": Words(
        Say(
            "More appointments were booked than {before}.",
            "More booked time is good for the business. Check you have the people to deliver it.",
        ),
        Say(
            "Fewer appointments were booked than {before}.",
            "Quiet spells happen. A reminder to regular customers can help fill the diary.",
        ),
    ),
    "bookings_vs_last_year_pct": Words(
        Say(
            "More appointments were booked than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons.",
        ),
        Say(
            "Fewer appointments were booked than at the same time last year.",
            "Comparing with the same time last year takes out the usual busy and quiet seasons, so this is worth a closer look.",
        ),
    ),
    "clients_seen": Words(
        Say(
            "You saw more clients than {before}.",
            "More people through the door is good. Make sure each one is asked to book again.",
        ),
        Say(
            "You saw fewer clients than {before}.",
            "Fewer people came in. A message to clients who have not been for a while can help.",
        ),
    ),
    "lapsed_clients": Words(
        Say(
            "More clients have gone quiet than {before}.",
            "These people came before and have not been back for three months. A friendly message can bring some back.",
        ),
        Say(
            "Fewer clients have gone quiet than {before}.",
            "That is a good sign: more of your clients are coming back.",
        ),
    ),
    "sales_per_appointment": Words(
        Say(
            "Each appointment brought in more than {before}.",
            "That includes product sales. Offering the right extra at the end of an appointment helps.",
        ),
        Say(
            "Each appointment brought in less than {before}.",
            "That includes product sales. Look at your prices and whether extras are being offered.",
        ),
    ),
    "cost_per_appointment": Words(
        Say(
            "Each appointment cost you more to deliver than {before}.",
            "That is all your running costs and the cost of goods spread over the appointments you kept. More appointments spread them thinner.",
        ),
        Say(
            "Each appointment cost you less to deliver than {before}.",
            "That is a good sign: your costs are spread over more appointments, or you are spending less.",
        ),
    ),
    "profit_per_appointment": Words(
        Say(
            "You kept more from each appointment than {before}.",
            "That is what is left after all your costs, divided by the appointments you kept.",
        ),
        Say(
            "You kept less from each appointment than {before}.",
            "That is what is left after all your costs, divided by the appointments you kept. Look at your prices and your biggest costs.",
        ),
    ),
    "messages_received": Words(
        Say(
            "Customers sent you more messages than {before}.",
            "More people are getting in touch. Make sure every message gets an answer.",
        ),
        Say(
            "Customers sent you fewer messages than {before}.",
            "Fewer people got in touch. Letting customers know they can message you can help.",
        ),
    ),
    "conversations_started": Words(
        Say(
            "More customers got in touch than {before}.",
            "These are people who wrote after a quiet spell, or for the first time.",
        ),
        Say(
            "Fewer customers got in touch than {before}.",
            "Fewer people started a conversation with you. Check your number is easy to find.",
        ),
    ),
    "replied_within_hour_pct": Words(
        Say(
            "You replied quickly to more of your new conversations than {before}.",
            "Customers who hear back within the hour are much more likely to go ahead.",
        ),
        Say(
            "You replied quickly to fewer of your new conversations than {before}.",
            "Customers who wait longer often go elsewhere. A quick first reply, even a short one, helps.",
        ),
    ),
    "average_reply_minutes": Words(
        Say(
            "Customers waited longer for a first reply than {before}.",
            "A short message saying you will get back to them soon keeps people waiting happily.",
        ),
        Say(
            "Customers waited less time for a first reply than {before}.",
            "That is a good sign: people hear back sooner.",
        ),
    ),
    "unanswered_conversations": Words(
        Say(
            "More conversations went without a reply than {before}.",
            "Every one is a customer who may have gone elsewhere. Check who is waiting to hear from you.",
        ),
        Say(
            "Fewer conversations went without a reply than {before}.",
            "That is a good sign: more customers are hearing back.",
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
VALUE_FIGURES = frozenset(
    {
        "revenue_growth_pct", "revenue_vs_last_year_pct", "sales_count_growth_pct",
        "sales_count_vs_last_year_pct", "units_growth_pct", "bookings_growth_pct",
        "bookings_vs_last_year_pct",
    }
)  # fmt: skip


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
