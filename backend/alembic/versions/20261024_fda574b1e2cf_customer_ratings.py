"""customer ratings on appointments, and the figures and action that answer them

Revision ID: fda574b1e2cf
Revises: c6e3b9a41d85
Create Date: 2026-10-24
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fda574b1e2cf"
down_revision: str | None = "c6e3b9a41d85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, description, category, unit, expression, direction)
KPIS = [
    ("happy_ratings_pct", "Customers who were happy",
     "Of the appointments customers rated, the share rated 4 or 5 out of 5.",
     "customer", "percent", "ratings_happy / bookings_rated * 100", "up_good"),
    ("unhappy_ratings_pct", "Customers who were unhappy",
     "Of the appointments customers rated, the share rated 1 or 2 out of 5.",
     "customer", "percent", "ratings_unhappy / bookings_rated * 100", "down_good"),
]  # fmt: skip

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)
ACTION = (
    "call_unhappy_customers",
    "Call the customers who gave a low rating",
    "customer",
    "An unhappy customer who is contacted quickly and kindly is often won back, and tells you what "
    "to fix. One who is left alone tends to leave without a word.",
    [
        "List the customers who rated you 1 or 2 recently",
        "Ring or message each one personally within a few days, to listen rather than to defend",
        "Put right what you can, and say what you will change",
        "Write down what they said, and look for the one thing that comes up most",
        "Check the share of unhappy customers again after a month",
    ],
    [{"kind": "figure_moved"}],
    ["happy_ratings_pct", "unhappy_ratings_pct"],
    ["improve_retention"],
    "low",
    "none",
    14,
    "0.300",
)


def upgrade() -> None:
    op.add_column("bookings", sa.Column("rating", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_bookings_rating_fits"),
        "bookings",
        "rating IS NULL OR (status = 'completed' AND rating BETWEEN 1 AND 5)",
    )
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
                "direction": direction, "requires": ["ratings"], "sort_order": 600 + index * 10,
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
    op.execute("DELETE FROM intervention_library WHERE code = 'call_unhappy_customers'")
    names = ", ".join(f"'{k[0]}'" for k in KPIS)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({names})")
    op.drop_constraint(op.f("ck_bookings_rating_fits"), "bookings", type_="check")
    op.drop_column("bookings", "rating")
