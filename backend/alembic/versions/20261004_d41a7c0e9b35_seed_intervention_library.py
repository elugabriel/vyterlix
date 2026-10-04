"""seed the intervention library

Revision ID: d41a7c0e9b35
Revises: be176f3d1b2e
Create Date: 2026-10-04
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d41a7c0e9b35"
down_revision: str | None = "be176f3d1b2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)

# code, name, category, summary, steps, addresses, kpis, goal types, effort, cost, days, impact share
LIBRARY = [
    (
        "promote_product",
        "Promote the product that has slipped",
        "sales",
        "Put a product that has sold less back in front of customers: a place at the counter or "
        "top of the page, a tasting or a short offer. It is the quickest way to win back sales you "
        "already know people wanted.",
        [
            "Move it to the most visible spot (counter, menu, front page)",
            "Run a short offer or a taster for two weeks",
            "Tell regulars by message or on social media",
            "Check its sales weekly and stop the offer if it recovers",
        ],
        [{"kind": "contributor", "dimension": "Product"}],
        ["revenue", "units_sold", "gross_profit"],
        ["increase_revenue", "improve_margin"],
        "low",
        "low",
        14,
        "0.300",
    ),
    (
        "bundle_offer",
        "Offer a bundle or add-on to lift the average sale",
        "sales",
        "When the average sale shrinks, a simple bundle (a drink with a pastry, a box of six) or "
        "a polite 'would you like' at the till brings the basket back up without needing more "
        "customers.",
        [
            "Pick two products that sell well together",
            "Price the pair slightly under buying them separately",
            "Train the team to offer it at every sale",
            "Compare the average sale before and after after a month",
        ],
        [{"kind": "sale_value"}, {"kind": "volume"}],
        ["revenue", "units_sold"],
        ["increase_revenue"],
        "low",
        "none",
        21,
        "0.200",
    ),
    (
        "win_back_regulars",
        "Win back the customers who stopped coming",
        "customer",
        "A customer you already have is the cheapest sale you will ever make. A friendly message to "
        "people who used to buy often and have gone quiet brings a good share of them back.",
        [
            "List customers who bought often before but not in the last month",
            "Send a short personal message with a small thank-you offer",
            "Follow up once with the ones who do not reply",
            "Note who comes back, so next time the offer can be aimed better",
        ],
        [{"kind": "contributor", "dimension": "Customer"}, {"kind": "sales_count"}],
        ["revenue", "sales_count"],
        ["improve_retention", "increase_revenue", "grow_customers"],
        "low",
        "low",
        21,
        "0.250",
    ),
    (
        "weekday_special",
        "Run a special on the day that has gone quiet",
        "sales",
        "If one day of the week has fallen away, give people a reason to come that day: a weekday "
        "special, a loyalty stamp that counts double, or an early-bird price.",
        [
            "Choose one offer for that day only",
            "Put it on the door, the website and your messages",
            "Run it for four weeks",
            "Keep it if the day recovers; swap it if not",
        ],
        [{"kind": "contributor", "dimension": "Day of the week"}, {"kind": "daily_rate"}],
        ["revenue", "sales_count"],
        ["increase_revenue"],
        "medium",
        "low",
        28,
        "0.250",
    ),
    (
        "channel_push",
        "Give the weaker sales channel a push",
        "sales",
        "When one way of selling (the shop, the website, a market stall) falls behind, a small, "
        "focused effort there is better than spreading effort thinly.",
        [
            "Find what changed in that channel (opening hours, stock, listings, pitch)",
            "Fix the most obvious problem first",
            "Add one promotion aimed only at that channel",
            "Review the channel's sales after four weeks",
        ],
        [{"kind": "contributor", "dimension": "Sales channel"}],
        ["revenue", "sales_count"],
        ["increase_revenue", "grow_customers"],
        "medium",
        "low",
        28,
        "0.250",
    ),
    (
        "price_review",
        "Review your prices and discounts",
        "financial",
        "When customers are paying less per item than before, find out whether discounts have crept "
        "in or prices have not kept up with costs, and set them deliberately.",
        [
            "List every product's price now against three months ago",
            "Find discounts given without a clear reason and stop them",
            "Raise prices on your best sellers by a small, round amount",
            "Watch sales for two weeks to see if volume holds",
        ],
        [{"kind": "price"}],
        ["revenue", "gross_profit"],
        ["improve_margin", "increase_revenue"],
        "low",
        "none",
        7,
        "0.400",
    ),
    (
        "supplier_review",
        "Get quotes and renegotiate with the supplier whose bill rose",
        "financial",
        "When one supplier's costs jump, ask them why, get a quote from one or two others, and "
        "either agree a better price or switch.",
        [
            "Compare this supplier's last three invoices item by item",
            "Ask them to explain the rise and to match a competing quote",
            "Get quotes from two other suppliers",
            "Agree a price in writing, or move the order",
        ],
        [{"kind": "contributor", "dimension": "Supplier"}],
        ["operating_expenses"],
        ["reduce_costs", "improve_margin"],
        "medium",
        "none",
        30,
        "0.300",
    ),
    (
        "cost_category_review",
        "Go through the cost that grew",
        "financial",
        "When one type of cost grows faster than the rest, look at every item in it: some will be "
        "waste, duplicates or things nobody uses.",
        [
            "List every payment in that cost type this month",
            "Mark anything unused, duplicated or higher than usual",
            "Cancel or cut the first three items that are not needed",
            "Set a monthly limit and check it each month",
        ],
        [{"kind": "contributor", "dimension": "Cost category"}],
        ["operating_expenses"],
        ["reduce_costs", "improve_margin"],
        "low",
        "none",
        14,
        "0.250",
    ),
    (
        "local_marketing",
        "Run a local marketing push",
        "marketing",
        "When fewer people are buying, remind the neighbourhood you are there: a leaflet drop, a "
        "local social media post, a partnership with a neighbouring business.",
        [
            "Choose one local channel you can reach cheaply",
            "Make one clear offer with an end date",
            "Track how many people mention it",
            "Repeat what works; drop what does not",
        ],
        [{"kind": "sales_count"}, {"kind": "daily_rate"}],
        ["revenue", "sales_count"],
        ["increase_revenue", "grow_customers"],
        "medium",
        "medium",
        21,
        "0.150",
    ),
    (
        "fix_product_detail",
        "Record what was sold on every sale",
        "operational",
        "Some sales have no product recorded, so we cannot say which products moved. Recording the "
        "product on every sale makes every later answer sharper. It does not win back money itself.",
        [
            "Make 'product' a required step when recording a sale",
            "Go back over the last month and add products to untagged sales",
            "Check the data overview for sales with no product detail",
        ],
        [{"kind": "no_product_detail"}],
        ["revenue", "gross_profit"],
        ["other"],
        "low",
        "none",
        7,
        "0.000",
    ),
]


def upgrade() -> None:
    table = sa.table(
        "intervention_library",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("summary", sa.String),
        sa.column("steps", sa.JSON),
        sa.column("addresses", sa.JSON),
        sa.column("kpis", sa.JSON),
        sa.column("goal_types", sa.JSON),
        sa.column("effort", sa.String),
        sa.column("cost_level", sa.String),
        sa.column("typical_days_to_effect", sa.SmallInteger),
        sa.column("impact_share", sa.Numeric),
        sa.column("impact_basis", sa.String),
        sa.column("version", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "category": category,
                "summary": summary,
                "steps": steps,
                "addresses": addresses,
                "kpis": kpis,
                "goal_types": goal_types,
                "effort": effort,
                "cost_level": cost,
                "typical_days_to_effect": days,
                "impact_share": share,
                "impact_basis": BASIS,
                "version": "1",
                "is_active": True,
            }
            for (
                code,
                name,
                category,
                summary,
                steps,
                addresses,
                kpis,
                goal_types,
                effort,
                cost,
                days,
                share,
            ) in LIBRARY
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM intervention_library")
