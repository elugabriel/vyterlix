"""health rules for bookings, rebooking, ratings and the website, and a weight for the operations area

Before this the health score had no way to judge a service business's appointments, rebooking or
ratings, nor a business's website, so a salon was scored mostly on figures made for shops. These
rules sit in the existing areas (operational, customer, marketing); whether an area counts for a
business is decided by what it sells (app/modules/rules.py), not here.

Revision ID: 751ad8f7b840
Revises: f5a29c7d13b8
Create Date: 2026-11-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "751ad8f7b840"
down_revision: str | None = "f5a29c7d13b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTING = "Starting value chosen by Vyterlix; replace with a sector benchmark when one is loaded."
VS_USUAL = "Compared with this business's own usual level, so no outside benchmark is needed."

# (category, kpi_code, basis, direction, bad, ok, good, weight, note, seasonal)
RULES = [
    ("operational", "no_show_rate_pct", "value", "down_good", 20, 8, 2, 3, STARTING, False),
    ("operational", "cancellation_rate_pct", "value", "down_good", 25, 12, 5, 2, STARTING, False),
    ("operational", "appointments_kept", "vs_baseline", "up_good", -20, 0, 10, 2, VS_USUAL, True),
    ("customer", "rebooking_rate_pct", "value", "up_good", 20, 45, 70, 3, STARTING, False),
    ("customer", "happy_ratings_pct", "value", "up_good", 50, 75, 90, 2, STARTING, False),
    ("marketing", "website_visits", "vs_baseline", "up_good", -25, 0, 15, 2, VS_USUAL, True),
    ("marketing", "website_actions", "vs_baseline", "up_good", -25, 0, 15, 3, VS_USUAL, True),
]  # fmt: skip
CODES = [r[1] for r in RULES]


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
    # Appointments are judged for businesses that take bookings, so the area now carries weight.
    op.execute(
        "UPDATE health_category_weights SET weight = 20"
        " WHERE category = 'operational' AND industry_code IS NULL"
    )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in CODES)
    op.execute(f"DELETE FROM health_rules WHERE kpi_code IN ({codes}) AND industry_code IS NULL")
    op.execute(
        "UPDATE health_category_weights SET weight = 0"
        " WHERE category = 'operational' AND industry_code IS NULL"
    )
