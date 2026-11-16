"""more figures for service businesses: clients seen, clients who have gone quiet, and what each
appointment brings in, costs and leaves

Revision ID: c52e81f0a7d3
Revises: b37d90e1f4a6
Create Date: 2026-11-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c52e81f0a7d3"
down_revision: str | None = "b37d90e1f4a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction, requires)
KPIS = [
    ("clients_seen", "Clients seen",
     "How many different customers had an appointment that went ahead in the month.",
     "customer", "count", "customers_kept", "up_good", ["bookings"]),
    ("lapsed_clients", "Clients who have gone quiet",
     "Clients who came in the past year but have not had an appointment for the last three months.",
     "customer", "count", "lapsed_clients", "down_good", ["bookings"]),
    ("sales_per_appointment", "Sales for each appointment",
     "All your sales, before VAT (products included), divided by the appointments that went ahead.",
     "financial", "gbp", "revenue / bookings_kept", "up_good", ["bookings", "sales"]),
    ("cost_per_appointment", "Cost of each appointment",
     "Your running costs and the cost of goods sold, divided by the appointments that went ahead.",
     "financial", "gbp", "(operating_expenses + cogs) / bookings_kept", "down_good",
     ["bookings", "sales", "expenses"]),
    ("profit_per_appointment", "Profit from each appointment",
     "What is left after all your costs, divided by the appointments that went ahead.",
     "financial", "gbp", "(revenue - cogs - operating_expenses) / bookings_kept", "up_good",
     ["bookings", "sales", "expenses"]),
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
                "sort_order": 1100 + index * 10,
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
