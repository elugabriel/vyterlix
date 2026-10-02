"""seed the first KPI definitions (financial and sales)

Revision ID: c7e41a90d3b8
Revises: db987911b622
Create Date: 2026-10-02
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e41a90d3b8"
down_revision: str | None = "db987911b622"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen on purpose: later edits to app code must not change what this migration inserted.
# New or changed KPIs get their own migration. Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction, requires)
KPIS = [
    ("revenue", "Sales", "Money from sales, before VAT, after taking off refunds.",
     "financial", "gbp", "revenue", "up_good", ["sales"]),
    ("gross_sales", "Takings including VAT",
     "What customers paid in total, VAT included, after refunds.",
     "financial", "gbp", "gross_sales", "up_good", ["sales"]),
    ("cost_of_goods_sold", "Cost of goods sold",
     "What the things you sold cost you to buy or make.",
     "financial", "gbp", "cogs", "neutral", ["sales"]),
    ("gross_profit", "Gross profit",
     "Sales minus the cost of the goods sold: what is left to pay your running costs.",
     "financial", "gbp", "revenue - cogs", "up_good", ["sales"]),
    ("gross_margin_pct", "Gross margin",
     "The share of every pound of sales left after the cost of the goods sold.",
     "financial", "percent", "(revenue - cogs) / revenue * 100", "up_good", ["sales"]),
    ("operating_expenses", "Running costs",
     "Rent, wages, utilities and other costs of running the business, not including stock bought.",
     "financial", "gbp", "operating_expenses", "down_good", ["expenses"]),
    ("net_profit", "Profit",
     "What the business made after the cost of goods and the running costs.",
     "financial", "gbp", "revenue - cogs - operating_expenses", "up_good", ["sales", "expenses"]),
    ("net_margin_pct", "Profit margin",
     "The share of every pound of sales that ends up as profit.",
     "financial", "percent", "(revenue - cogs - operating_expenses) / revenue * 100", "up_good",
     ["sales", "expenses"]),
    ("stock_purchases", "Stock bought",
     "What you spent buying stock, from the costs you marked as cost of sales.",
     "financial", "gbp", "stock_purchases", "neutral", ["expenses"]),
    ("sales_count", "Number of sales", "How many separate sales you made.",
     "sales", "count", "sales_count", "up_good", ["sales"]),
    ("average_order_value", "Average sale",
     "The average amount of each sale, before VAT and before refunds.",
     "sales", "gbp", "sales_revenue / sales_count", "up_good", ["sales"]),
    ("units_sold", "Items sold", "How many items you sold, after refunds.",
     "sales", "count", "units_sold", "up_good", ["sales"]),
    ("refund_rate_pct", "Refund rate", "For every 100 sales, how many were refunded.",
     "sales", "percent", "refund_count / sales_count * 100", "down_good", ["sales"]),
    ("revenue_growth_pct", "Sales growth",
     "How much your sales grew (or shrank) compared with the period before.",
     "sales", "percent", "(revenue - prev(revenue)) / prev(revenue) * 100", "up_good", ["sales"]),
    ("revenue_vs_last_year_pct", "Sales compared with last year",
     "How your sales compare with the same period a year ago.",
     "sales", "percent", "(revenue - yoy(revenue)) / yoy(revenue) * 100", "up_good", ["sales"]),
]  # fmt: skip


def upgrade() -> None:
    table = sa.table(
        "kpi_definitions",
        sa.column("id", sa.Uuid),
        sa.column("code"),
        sa.column("name"),
        sa.column("description"),
        sa.column("category"),
        sa.column("unit"),
        sa.column("expression"),
        sa.column("direction"),
        sa.column("requires", sa.JSON),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "description": description,
                "category": category,
                "unit": unit,
                "expression": expression,
                "direction": direction,
                "requires": requires,
                "sort_order": (index + 1) * 10,
            }
            for index, (
                code,
                name,
                description,
                category,
                unit,
                expression,
                direction,
                requires,
            ) in enumerate(KPIS)
        ],
    )


def downgrade() -> None:
    codes = ", ".join(f"'{k[0]}'" for k in KPIS)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
