"""appointment recommendations: reminders, cancellation policy, rebooking prompt

Revision ID: c4f19e7a2d58
Revises: b8d2f4a61c93
Create Date: 2026-10-18
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f19e7a2d58"
down_revision: str | None = "b8d2f4a61c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)

# code, name, category, summary, steps, addresses, kpis, goal types, effort, cost, days, impact share, basis
FIGURE = [{"kind": "figure_moved"}]
LOSSES = ["cancellation_rate_pct", "no_show_rate_pct", "late_cancellations"]
LIBRARY = [
    (
        "appointment_reminders",
        "Send a reminder the day before every appointment",
        "customer",
        "A short message a day or two before is the simplest way to cut missed and cancelled "
        "appointments, and it gives people the chance to cancel early enough for you to fill the slot.",
        [
            "Choose how to remind people: text, WhatsApp or email",
            "Send it 24 to 48 hours before, with a way to confirm or move the appointment",
            "Keep the gap freed by an early cancellation open for someone else",
            "After four weeks compare late cancellations and no-shows with the month before, and "
            "ask a few customers whether the reminders felt helpful or pushy",
        ],
        FIGURE,
        LOSSES,
        ["increase_revenue", "improve_retention"],
        "low",
        "low",
        28,
        "0.300",
        BASIS,
    ),
    (
        "cancellation_policy",
        "Set and share a clear cancellation policy",
        "customer",
        "When people know how much notice you need, and what happens if they do not give it, "
        "fewer cancel at the last minute. Keep it fair: a harsh policy can lose good customers.",
        [
            "Write the policy in one or two sentences (for example: 24 hours notice)",
            "Put it where people book and in the reminder",
            "Decide in advance when you will make an exception",
            "After four weeks compare late cancellations, and check regular customers are still happy",
        ],
        FIGURE,
        LOSSES,
        ["increase_revenue", "improve_retention"],
        "low",
        "none",
        28,
        "0.250",
        BASIS,
    ),
    (
        "rebooking_prompt",
        "Offer the next appointment before they leave",
        "customer",
        "Customers who leave with their next appointment already booked come back far more often "
        "than those who are left to remember. A reminder at the usual gap between visits helps the rest.",
        [
            "Ask every customer at the end of the visit: shall we book your next one now?",
            "Work out the usual gap between visits and send a reminder as it comes round",
            "Keep a simple count of who was offered and who booked",
            "After a month record whether the reminders or the staff prompts worked better",
        ],
        FIGURE,
        ["rebooking_rate_pct", "appointments_kept"],
        ["improve_retention", "increase_revenue"],
        "low",
        "none",
        30,
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
