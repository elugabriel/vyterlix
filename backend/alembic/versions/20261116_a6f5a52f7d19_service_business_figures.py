"""figures for service businesses: bookings, retention, operations and the team's time

New figures worked out from appointments (booked, completion rate, visits per customer, new
clients), from how long appointments took (average length, how often they ran over) and from the
hours staff worked (hours, sales per hour, how much of the time was booked, appointments per hour),
plus health rules for the ones that say how well the work is going.

Revision ID: a6f5a52f7d19
Revises: 9c3e5a17d2b4
Create Date: 2026-11-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6f5a52f7d19"
down_revision: str | None = "9c3e5a17d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTING = "Starting value chosen by Vyterlix; replace with a sector benchmark when one is loaded."

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, category, unit, expression, direction, requires)
KPIS = [
    ("appointments_booked", "Appointments booked",
     "All the appointments in the month, whether kept, cancelled, missed or still to come.",
     "sales", "count", "bookings_all", "up_good", ["bookings"]),
    ("completion_rate_pct", "Appointments completed",
     "Of the appointments that were due, the share that went ahead.",
     "operational", "percent", "bookings_kept / bookings_finished * 100", "up_good", ["bookings"]),
    ("visits_per_customer", "Visits per customer",
     "How many appointments each customer kept, on average, in the month.",
     "customer", "count", "bookings_kept_known / customers_kept", "up_good", ["bookings"]),
    ("new_clients", "New clients",
     "Customers whose first kept appointment was in the month.",
     "customer", "count", "new_clients", "up_good", ["bookings"]),
    ("average_appointment_minutes", "Average appointment length (minutes)",
     "How long an appointment took, on average, for the ones with a time typed in.",
     "operational", "count", "minutes_taken / timed_kept", "neutral", ["timed"]),
    ("overrun_pct", "Appointments that ran over",
     "Of the appointments with a planned and an actual time, the share that took longer than planned.",
     "operational", "percent", "timed_over / timed_both * 100", "down_good", ["timed"]),
    ("hours_worked", "Hours worked",
     "The hours your team worked in the month, as typed in.",
     "operational", "count", "hours_worked", "neutral", ["staff_hours"]),
    ("revenue_per_hour", "Sales per hour worked",
     "Your sales, before VAT, divided by the hours your team worked.",
     "operational", "gbp", "revenue / hours_worked", "up_good", ["sales", "staff_hours"]),
    ("utilisation_pct", "Time spent on appointments",
     "Of the hours your team worked, the share spent on appointments that went ahead.",
     "operational", "percent", "minutes_taken / 60 / hours_worked * 100", "up_good",
     ["timed", "staff_hours"]),
    ("appointments_per_hour", "Appointments per hour worked",
     "Appointments kept divided by the hours your team worked.",
     "operational", "count", "bookings_kept / hours_worked", "up_good", ["bookings", "staff_hours"]),
]  # fmt: skip
CODES = [k[0] for k in KPIS]

# (category, kpi_code, basis, direction, bad, ok, good, weight, note, seasonal)
RULES = [
    ("operational", "completion_rate_pct", "value", "up_good", 70, 85, 95, 2, STARTING, False),
    ("operational", "overrun_pct", "value", "down_good", 50, 25, 10, 1, STARTING, False),
    ("operational", "utilisation_pct", "value", "up_good", 40, 65, 85, 2, STARTING, False),
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
                "sort_order": 800 + index * 10,
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
        sa.column("seasonal", sa.Boolean),
    )
    op.bulk_insert(
        rules,
        [
            {
                "id": uuid.uuid4(),
                "category": category,
                "kpi_code": code,
                "basis": basis,
                "direction": direction,
                "threshold_bad": bad,
                "threshold_ok": ok,
                "threshold_good": good,
                "weight": weight,
                "note": note,
                "seasonal": seasonal,
            }
            for category, code, basis, direction, bad, ok, good, weight, note, seasonal in RULES
        ],
    )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in CODES)
    op.execute(f"DELETE FROM health_rules WHERE kpi_code IN ({codes})")
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
