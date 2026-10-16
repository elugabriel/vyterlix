"""more recommendations: slow stock, protect the best seller, valuable customer at risk

Revision ID: a3e7c1d94b20
Revises: d35a1b467617
Create Date: 2026-10-17
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3e7c1d94b20"
down_revision: str | None = "d35a1b467617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)
NO_ESTIMATE = (
    "No amount is promised: this protects something that is already working, so it is judged by "
    "whether the good result held, not by money won back."
)

# code, name, category, summary, steps, addresses, kpis, goal types, effort, cost, days, impact share, basis
LIBRARY = [
    (
        "slow_stock_review",
        "Review the slow-moving stock before you reorder",
        "operational",
        "A product that sells slowly while the shelf is full ties up money. Before the next order, "
        "decide which of three things suits it: order less or pause the order, review the price, or "
        "run a limited promotion. Which suits depends on its margin and how fast it really sells.",
        [
            "Check how many are in stock and how many you sell in a month",
            "Look at the margin: a thin margin leaves little room for a promotion",
            "Reduce or pause the next supplier order if there is more than three months of stock",
            "If you run a promotion, keep it short and limited, and note what it cost in profit",
            "Check stock, sales and profit again after two weeks. If a promotion did not help, "
            "do not repeat it automatically",
        ],
        [{"kind": "contributor", "dimension": "Product", "tag": "much_stock"}],
        ["revenue", "units_sold", "gross_profit"],
        ["reduce_costs", "improve_margin"],
        "low",
        "none",
        14,
        "0.200",
        BASIS,
    ),
    (
        "protect_best_seller",
        "Protect your best seller: check stock and avoid needless discounts",
        "sales",
        "A product that is selling well is an opportunity and a risk: it can run out, and a "
        "discount on something people already want gives profit away. Make sure you have enough, "
        "and tell the people who bought it before.",
        [
            "Check the stock against what you expect to sell next month, and reorder if short",
            "Message people who bought it before, with a reason to buy again (no discount needed)",
            "Do not discount it while demand is strong",
            "After two weeks check stock, how many took up the offer, and profit, not sales alone",
        ],
        [{"kind": "contributor", "dimension": "Product", "effect": "good"}],
        ["revenue", "units_sold", "gross_profit"],
        ["increase_revenue", "improve_margin"],
        "low",
        "none",
        14,
        "0.000",
        NO_ESTIMATE,
    ),
    (
        "valuable_customer_at_risk",
        "Contact the valuable customer who is buying less",
        "customer",
        "One of your best customers is still buying, but less than before. A personal call from "
        "the person who knows them is worth more than any offer. Do it in the next three days.",
        [
            "Ask the person who looks after this customer to contact them within three days",
            "Ask how things are going and whether anything has changed for them",
            "Write down what they say, and whether you spoke at all",
            "Look at their orders over the next two months. Do not count them as saved until "
            "their orders have actually recovered",
        ],
        [{"kind": "contributor", "dimension": "Customer", "tag": "still_buying"}],
        ["revenue", "sales_count"],
        ["improve_retention", "increase_revenue"],
        "low",
        "none",
        3,
        "0.300",
        BASIS,
    ),
]

CODES = [row[0] for row in LIBRARY]


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
                "impact_basis": basis,
                "version": "1",
                "is_active": True,
            }
            for (
                code, name, category, summary, steps, addresses, kpis, goal_types, effort, cost,
                days, share, basis,
            ) in LIBRARY
        ],
    )  # fmt: skip


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM intervention_library WHERE code = ANY(:codes)").bindparams(codes=CODES)
    )
