"""figures for Supplier Performance and Demand Forecasting

Supplier figures come from the costs that name a supplier (how much was bought, from how many
suppliers, how much of it from the biggest one, how much was given back as credit). Demand figures
compare how many sales, items and appointments there were with the period before and with the same
time last year. No health rules: there is no fair starting value for how much buying from one
supplier is too much.

Revision ID: b37d90e1f4a6
Revises: d81f6a3c5e90
Create Date: 2026-11-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b37d90e1f4a6"
down_revision: str | None = "d81f6a3c5e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction, requires)
KPIS = [
    ("supplier_bought", "Bought from suppliers",
     "What you spent with suppliers, before VAT, on the costs that name one.",
     "financial", "gbp", "supplier_bought", "neutral", ["suppliers"]),
    ("suppliers_used", "Suppliers you bought from",
     "How many different suppliers you bought from in the month.",
     "financial", "count", "suppliers_used", "neutral", ["suppliers"]),
    ("top_supplier_share_pct", "Buying from your biggest supplier",
     "Of everything you bought from suppliers, the share that went to the one you spent most with.",
     "financial", "percent", "top_supplier_spend / supplier_bought * 100", "down_good",
     ["suppliers"]),
    ("supplier_credit_rate_pct", "Money given back by suppliers",
     "Of what you bought, the share suppliers gave back as credit, usually for goods that were "
     "wrong or sent back.",
     "financial", "percent", "supplier_credits / supplier_bought * 100", "down_good",
     ["suppliers"]),
    ("sales_count_growth_pct", "Number of sales, compared with before",
     "How the number of sales (not their value) changed compared with the period before.",
     "sales", "percent", "(sales_count - prev(sales_count)) / prev(sales_count) * 100", "up_good",
     ["sales"]),
    ("sales_count_vs_last_year_pct", "Number of sales, compared with last year",
     "How the number of sales compares with the same period a year ago.",
     "sales", "percent", "(sales_count - yoy(sales_count)) / yoy(sales_count) * 100", "up_good",
     ["sales"]),
    ("units_growth_pct", "Items sold, compared with before",
     "How the number of items sold changed compared with the period before.",
     "sales", "percent", "(units_sold - prev(units_sold)) / prev(units_sold) * 100", "up_good",
     ["sales"]),
    ("bookings_growth_pct", "Appointments booked, compared with before",
     "How the number of appointments booked changed compared with the period before.",
     "sales", "percent",
     "(bookings_all - prev(bookings_all)) / prev(bookings_all) * 100", "up_good", ["bookings"]),
    ("bookings_vs_last_year_pct", "Appointments booked, compared with last year",
     "How the number of appointments booked compares with the same period a year ago.",
     "sales", "percent",
     "(bookings_all - yoy(bookings_all)) / yoy(bookings_all) * 100", "up_good", ["bookings"]),
]  # fmt: skip
CODES = [k[0] for k in KPIS]


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
                "sort_order": 1000 + index * 10,
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
    codes = ", ".join(f"'{c}'" for c in CODES)
    op.execute(
        f"DELETE FROM kpi_values WHERE kpi_id IN (SELECT id FROM kpi_definitions WHERE code IN ({codes}))"
    )
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
