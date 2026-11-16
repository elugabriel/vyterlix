"""health rules for how fast messages are answered, how much a business leans on one supplier, how
much suppliers give back, clients who have gone quiet, and sales and appointments against last year

Until now these figures were shown but did not count towards the Business Health Score. They do now,
for the businesses that have them (a figure with no data, or in a part the owner has switched off,
is never judged).

Not given a rule, on purpose: counts of things that are neither good nor bad on their own (messages
received, new conversations, how much was bought, how many suppliers), and the month-on-month
growth figures, which swing with the seasons. Growth against the same month last year is judged.

Revision ID: a96c25e0f3b7
Revises: f85b14d9e2c6
Create Date: 2026-11-17
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a96c25e0f3b7"
down_revision: str | None = "f85b14d9e2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTING = "Starting value chosen by Vyterlix; replace with a sector benchmark when one is loaded."
VS_USUAL = "Compared with this business's own usual level, so no outside benchmark is needed."
LAST_YEAR = "Growth on the same period last year; 0 means level with last year."

# (category, kpi_code, basis, direction, bad, ok, good, weight, note, seasonal)
# The score is 0 at `bad`, 60 at `ok` and 100 at `good`, in a straight line between them.
RULES = [
    ("operational", "replied_within_hour_pct", "value", "up_good", 30, 60, 85, 2, STARTING, False),
    ("operational", "average_reply_minutes", "value", "down_good", 240, 60, 15, 1, STARTING, False),
    ("operational", "unanswered_conversations", "vs_baseline", "down_good", 50, 0, -25, 1,
     VS_USUAL, False),
    ("financial", "top_supplier_share_pct", "value", "down_good", 85, 60, 40, 1,
     "Leaning on one supplier is a risk if they put prices up or let you down. " + STARTING, False),
    ("financial", "supplier_credit_rate_pct", "value", "down_good", 12, 5, 1, 1, STARTING, False),
    ("sales", "sales_count_vs_last_year_pct", "value", "up_good", -15, 0, 10, 1, LAST_YEAR, False),
    ("sales", "bookings_vs_last_year_pct", "value", "up_good", -15, 0, 10, 1, LAST_YEAR, False),
    ("customer", "lapsed_clients", "vs_baseline", "down_good", 50, 0, -20, 1, VS_USUAL, False),
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


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in CODES)
    op.execute(f"DELETE FROM health_rules WHERE kpi_code IN ({codes})")
