"""seed the customer and stock KPI definitions

Revision ID: e5b2a817c4f6
Revises: c7e41a90d3b8
Create Date: 2026-10-03
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2a817c4f6"
down_revision: str | None = "c7e41a90d3b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction, requires)
KPIS = [
    ("active_customers", "Customers who bought", "How many different customers bought from you.",
     "customer", "count", "active_customers", "up_good", ["customer_sales"]),
    ("new_customers", "New customers", "Customers buying from you for the first time.",
     "customer", "count", "new_customers", "up_good", ["customer_sales"]),
    ("returning_customers", "Returning customers", "Customers who had bought from you before.",
     "customer", "count", "active_customers - new_customers", "up_good", ["customer_sales"]),
    ("repeat_customer_rate_pct", "Repeat customers",
     "Of the customers who bought, the share who had bought from you before.",
     "customer", "percent", "(active_customers - new_customers) / active_customers * 100",
     "up_good", ["customer_sales"]),
    ("customer_retention_pct", "Customers who came back",
     "Of the customers who bought in the period before, the share who bought again.",
     "customer", "percent", "retained_customers / prev(active_customers) * 100", "up_good",
     ["customer_sales"]),
    ("customer_churn_pct", "Customers who did not come back",
     "Of the customers who bought in the period before, the share who did not buy this time.",
     "customer", "percent",
     "(prev(active_customers) - retained_customers) / prev(active_customers) * 100", "down_good",
     ["customer_sales"]),
    ("average_customer_value", "Spend per customer",
     "The average amount each known customer spent in the period, before VAT.",
     "customer", "gbp", "identified_revenue / active_customers", "up_good", ["customer_sales"]),
    ("customers_identified_pct", "Sales linked to a customer",
     "How many sales say who bought. The higher this is, the more the customer figures can be trusted.",
     "customer", "percent", "identified_sales / sales_count * 100", "neutral", ["sales"]),
    ("stock_units", "Items in stock", "How many items you held in stock at the end of the period.",
     "inventory", "count", "stock_units", "neutral", ["stock"]),
    ("stock_value", "Value of stock",
     "What the stock you held at the end of the period cost you.",
     "inventory", "gbp", "stock_value", "neutral", ["stock"]),
    ("stock_turnover", "Stock turnover",
     "How many times your stock was sold and replaced in the period: the cost of goods sold divided by the stock you hold.",
     "inventory", "ratio", "cogs / stock_value", "up_good", ["sales", "stock"]),
    ("days_of_stock", "Days of stock",
     "At the rate you are selling, how many days the stock you hold would last.",
     "inventory", "count", "stock_value / cogs * period_days", "neutral", ["sales", "stock"]),
    ("out_of_stock_pct", "Products out of stock",
     "The share of the products you track that had none left at the end of the period.",
     "inventory", "percent", "products_out_of_stock / products_tracked * 100", "down_good",
     ["stock"]),
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
                "sort_order": 200 + index * 10,
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
