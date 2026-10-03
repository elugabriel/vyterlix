"""seed the starting health rules and category weights

Revision ID: f2d8c6b1a937
Revises: 43f0669aba3e
Create Date: 2026-10-03
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2d8c6b1a937"
down_revision: str | None = "43f0669aba3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTING = "Starting value chosen by Vyterlix; replace with a sector benchmark when one is loaded."
VS_USUAL = "Compared with this business's own usual level, so no outside benchmark is needed."

# (category, kpi_code, basis, direction, bad, ok, good, weight, note)
# The score is 0 at `bad`, 60 at `ok` and 100 at `good`, in a straight line between them.
# For basis "vs_baseline" the numbers are the % difference from the business's usual level.
RULES = [
    ("financial", "net_margin_pct", "value", "up_good", 0, 8, 15, 3, STARTING),
    ("financial", "gross_margin_pct", "value", "up_good", 30, 45, 60, 2, STARTING),
    ("financial", "net_profit", "vs_baseline", "up_good", -30, 0, 15, 2, VS_USUAL),
    ("financial", "operating_expenses", "vs_baseline", "down_good", 20, 0, -5, 1, VS_USUAL),
    ("sales", "revenue", "vs_baseline", "up_good", -20, 0, 10, 3, VS_USUAL),
    ("sales", "revenue_vs_last_year_pct", "value", "up_good", -15, 0, 10, 2,
     "Growth on the same period last year; 0 means level with last year."),
    ("sales", "average_order_value", "vs_baseline", "up_good", -15, 0, 8, 1, VS_USUAL),
    ("sales", "refund_rate_pct", "value", "down_good", 8, 3, 1, 1, STARTING),
    ("customer", "customer_retention_pct", "value", "up_good", 30, 50, 70, 3, STARTING),
    ("customer", "active_customers", "vs_baseline", "up_good", -20, 0, 10, 2, VS_USUAL),
    ("customer", "repeat_customer_rate_pct", "value", "up_good", 20, 40, 60, 1, STARTING),
    ("customer", "average_customer_value", "vs_baseline", "up_good", -15, 0, 8, 1, VS_USUAL),
    ("inventory", "out_of_stock_pct", "value", "down_good", 25, 10, 0, 2, STARTING),
    ("inventory", "stock_turnover", "vs_baseline", "up_good", -30, 0, 15, 1, VS_USUAL),
]  # fmt: skip

# How much each area counts towards the overall score. Marketing and operational have no
# metrics yet (they need Google Analytics and the actions engine), so they are left out of the
# score until they do, and the score says how much of the picture it covers.
CATEGORY_WEIGHTS = {
    "financial": 35,
    "sales": 25,
    "customer": 20,
    "marketing": 10,
    "inventory": 10,
    "operational": 0,
}


def upgrade() -> None:
    rules = sa.table(
        "health_rules",
        sa.column("id", sa.Uuid),
        sa.column("category"),
        sa.column("kpi_code"),
        sa.column("basis"),
        sa.column("direction"),
        sa.column("threshold_bad", sa.Numeric),
        sa.column("threshold_ok", sa.Numeric),
        sa.column("threshold_good", sa.Numeric),
        sa.column("weight", sa.Numeric),
        sa.column("note"),
    )
    op.bulk_insert(
        rules,
        [
            {
                "id": uuid.uuid4(),
                "category": category,
                "kpi_code": kpi_code,
                "basis": basis,
                "direction": direction,
                "threshold_bad": bad,
                "threshold_ok": ok,
                "threshold_good": good,
                "weight": weight,
                "note": note,
            }
            for category, kpi_code, basis, direction, bad, ok, good, weight, note in RULES
        ],
    )
    weights = sa.table(
        "health_category_weights",
        sa.column("id", sa.Uuid),
        sa.column("category"),
        sa.column("weight", sa.Numeric),
    )
    op.bulk_insert(
        weights,
        [
            {"id": uuid.uuid4(), "category": category, "weight": weight}
            for category, weight in CATEGORY_WEIGHTS.items()
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM health_rules WHERE industry_code IS NULL")
    op.execute("DELETE FROM health_category_weights WHERE industry_code IS NULL")
