"""the invoice figures and the action that answers them

Revision ID: f1c8a3d49e07
Revises: 88248b978c6f
Create Date: 2026-10-21
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c8a3d49e07"
down_revision: str | None = "88248b978c6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, description, category, unit, expression, direction)
KPIS = [
    ("money_owed", "Money owed to you",
     "What customers owed you at the end of the period for invoices not yet paid, including VAT.",
     "financial", "gbp", "invoices_outstanding", "down_good"),
    ("overdue_invoices", "Overdue invoices",
     "The part of that which was past its due day at the end of the period.",
     "financial", "gbp", "invoices_overdue", "down_good"),
    ("days_to_get_paid", "Days to get paid",
     "On average, how many days the invoices paid in the period took to be paid.",
     "financial", "count", "average_days_to_pay", "down_good"),
]  # fmt: skip

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)
ACTION = (
    "chase_overdue_invoices",
    "Chase the overdue invoices and tighten how you ask to be paid",
    "financial",
    "Money you are owed is a free loan to your customers. A friendly reminder on the day an invoice "
    "falls due, and a polite call a week later, brings most of it in. Shorter terms or a deposit "
    "stop it building up again.",
    [
        "List the overdue invoices, the largest first",
        "Send a friendly reminder, then ring the ones still unpaid after a week",
        "Agree a firm date with anyone who says they will pay late",
        "For next time: shorter payment terms, a deposit, or a reminder on the due day",
        "Check the overdue total again in a month, and whether anyone you chased has stopped buying",
    ],
    [{"kind": "figure_moved"}],
    ["money_owed", "overdue_invoices", "days_to_get_paid"],
    ["improve_cash_flow", "other"],
    "low",
    "none",
    21,
    "0.300",
)


def upgrade() -> None:
    kpi = sa.table(
        "kpi_definitions",
        sa.column("id", sa.Uuid), sa.column("code"), sa.column("name"), sa.column("description"),
        sa.column("category"), sa.column("unit"), sa.column("expression"), sa.column("direction"),
        sa.column("requires", sa.JSON), sa.column("sort_order", sa.SmallInteger),
    )  # fmt: skip
    op.bulk_insert(
        kpi,
        [
            {
                "id": uuid.uuid4(), "code": code, "name": name, "description": description,
                "category": category, "unit": unit, "expression": expression,
                "direction": direction, "requires": ["invoices"], "sort_order": 400 + index * 10,
            }
            for index, (code, name, description, category, unit, expression, direction) in enumerate(KPIS)
        ],
    )  # fmt: skip
    library = sa.table(
        "intervention_library",
        sa.column("id", sa.Uuid), sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("category", sa.String), sa.column("summary", sa.String),
        sa.column("steps", sa.JSON), sa.column("addresses", sa.JSON), sa.column("kpis", sa.JSON),
        sa.column("goal_types", sa.JSON), sa.column("effort", sa.String),
        sa.column("cost_level", sa.String), sa.column("typical_days_to_effect", sa.SmallInteger),
        sa.column("impact_share", sa.Numeric), sa.column("impact_basis", sa.String),
        sa.column("version", sa.String), sa.column("is_active", sa.Boolean),
    )  # fmt: skip
    (
        code,
        name,
        category,
        summary,
        steps,
        addresses,
        kpis,
        goal_types,
        effort,
        cost,
        days,
        share,
    ) = ACTION
    op.bulk_insert(
        library,
        [
            {
                "id": uuid.uuid4(), "code": code, "name": name, "category": category,
                "summary": summary, "steps": steps, "addresses": addresses, "kpis": kpis,
                "goal_types": goal_types, "effort": effort, "cost_level": cost,
                "typical_days_to_effect": days, "impact_share": share, "impact_basis": BASIS,
                "version": "1", "is_active": True,
            }
        ],
    )  # fmt: skip


def downgrade() -> None:
    op.execute("DELETE FROM intervention_library WHERE code = 'chase_overdue_invoices'")
    codes = ", ".join(f"'{k[0]}'" for k in KPIS)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
