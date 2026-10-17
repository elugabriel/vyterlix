"""the lead, proposal and renewal figures and the actions that answer them

Revision ID: a5d2e8c3b714
Revises: 96d0361e9c67
Create Date: 2026-10-22
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5d2e8c3b714"
down_revision: str | None = "96d0361e9c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, description, category, unit, expression, direction)
KPIS = [
    ("new_leads", "New leads", "How many new enquiries came in.",
     "sales", "count", "leads_in", "up_good"),
    ("proposals_sent", "Proposals sent", "How many proposals or quotes you sent.",
     "sales", "count", "proposals_sent", "up_good"),
    ("win_rate_pct", "Proposals won",
     "Of the proposals that were decided, the share you won. Ones still being chased do not count.",
     "sales", "percent", "deals_won / deals_decided * 100", "up_good"),
    ("work_won", "Work won", "The value of the work you won, before VAT.",
     "sales", "gbp", "value_won", "up_good"),
    ("renewal_rate_pct", "Clients who renewed",
     "Of the renewals that were decided, the share where the client stayed.",
     "customer", "percent", "renewals_won / renewals_decided * 100", "up_good"),
]  # fmt: skip

BASIS = (
    "Starting estimate chosen by Vyterlix: the share of the gap this usually wins back. "
    "To be replaced by what really happened once outcomes are tracked."
)
FIGURE = [{"kind": "figure_moved"}]
# (code, name, category, summary, steps, kpis, goal types, effort, cost, days, impact share)
ACTIONS = [
    (
        "review_lost_proposals",
        "Look back at the proposals you lost",
        "sales",
        "When fewer proposals are won, the quickest lesson is in the ones you lost. Ask a few of "
        "those clients, kindly, what decided it: price, timing, trust, or someone else's offer.",
        [
            "List the proposals you lost recently",
            "Ask three of those clients what made the difference (a short call or message)",
            "Look for the one reason that comes up most, and change that first",
            "Check the share you win again after a month",
        ],
        ["win_rate_pct", "work_won"],
        ["increase_revenue"],
        "low", "none", 30, "0.250",
    ),
    (
        "ask_for_introductions",
        "Ask your best clients for introductions",
        "sales",
        "When fewer enquiries arrive, the cheapest place to find more is the people who already "
        "like your work. A short, specific request to a handful of past clients often brings leads.",
        [
            "Pick five clients who were happy with your work",
            "Ask each one personally who else they know who needs the same help",
            "Make it easy: offer to write the introduction for them to send",
            "Count the new leads that come from it over a month",
        ],
        ["new_leads", "proposals_sent"],
        ["increase_revenue", "grow_customers"],
        "low", "none", 30, "0.250",
    ),
    (
        "talk_before_renewal",
        "Talk to clients before their renewal is due",
        "customer",
        "Clients who leave at renewal usually decided long before. A conversation a month or two "
        "ahead, about how it has gone and what they need next, saves more renewals than a "
        "reminder on the day.",
        [
            "List the clients whose agreements end in the next two months",
            "Book a short review call with each: what has worked, what has not, what next",
            "Fix anything that is bothering them before asking them to renew",
            "Record who renewed and, for those who did not, why",
        ],
        ["renewal_rate_pct"],
        ["improve_retention"],
        "low", "none", 30, "0.300",
    ),
]  # fmt: skip


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
                "direction": direction, "requires": ["pipeline"], "sort_order": 500 + index * 10,
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
    op.bulk_insert(
        library,
        [
            {
                "id": uuid.uuid4(), "code": code, "name": name, "category": category,
                "summary": summary, "steps": steps, "addresses": FIGURE, "kpis": kpis,
                "goal_types": goal_types, "effort": effort, "cost_level": cost,
                "typical_days_to_effect": days, "impact_share": share, "impact_basis": BASIS,
                "version": "1", "is_active": True,
            }
            for (code, name, category, summary, steps, kpis, goal_types, effort, cost, days, share) in ACTIONS
        ],
    )  # fmt: skip


def downgrade() -> None:
    codes = ", ".join(f"'{a[0]}'" for a in ACTIONS)
    op.execute(f"DELETE FROM intervention_library WHERE code IN ({codes})")
    names = ", ".join(f"'{k[0]}'" for k in KPIS)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({names})")
