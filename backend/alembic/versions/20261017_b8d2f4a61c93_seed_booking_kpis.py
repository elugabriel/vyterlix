"""seed the booking KPI definitions

Revision ID: b8d2f4a61c93
Revises: a056fb1b6548
Create Date: 2026-10-17
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f4a61c93"
down_revision: str | None = "a056fb1b6548"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction)
KPIS = [
    ("appointments_kept", "Appointments kept", "How many appointments went ahead.",
     "sales", "count", "bookings_kept", "up_good"),
    ("cancellation_rate_pct", "Appointments cancelled",
     "Of the appointments that were due, the share that were cancelled.",
     "customer", "percent", "bookings_cancelled / bookings_finished * 100", "down_good"),
    ("no_show_rate_pct", "Appointments missed",
     "Of the appointments that were due, the share where the customer did not turn up.",
     "customer", "percent", "bookings_missed / bookings_finished * 100", "down_good"),
    ("late_cancellations", "Late cancellations",
     "Appointments cancelled the day before or on the day, too late to fill the gap.",
     "customer", "count", "late_cancellations", "down_good"),
    ("rebooking_rate_pct", "Customers who booked again",
     "Of the customers who kept an appointment, the share who have another one booked after it.",
     "customer", "percent", "customers_rebooked / customers_kept * 100", "up_good"),
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
                "requires": ["bookings"],
                "sort_order": 300 + index * 10,
            }
            for index, (
                code,
                name,
                description,
                category,
                unit,
                expression,
                direction,
            ) in enumerate(KPIS)
        ],
    )


def downgrade() -> None:
    codes = ", ".join(f"'{k[0]}'" for k in KPIS)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
