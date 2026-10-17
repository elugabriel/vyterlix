"""win back regulars also answers a fall in customers who come back

Revision ID: d7a3e5b18f42
Revises: c4f19e7a2d58
Create Date: 2026-10-19
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3e5b18f42"
down_revision: str | None = "c4f19e7a2d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KPIS = ["revenue", "sales_count"]
OLD_ADDRESSES = [{"kind": "contributor", "dimension": "Customer"}, {"kind": "sales_count"}]
RETURNING = ["customer_retention_pct", "repeat_customer_rate_pct", "customer_churn_pct"]


def _set(kpis: list, addresses: list) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE intervention_library SET kpis = CAST(:kpis AS json), "
            "addresses = CAST(:addresses AS json) WHERE code = 'win_back_regulars'"
        ),
        {"kpis": json.dumps(kpis), "addresses": json.dumps(addresses)},
    )


def upgrade() -> None:
    # The client's own example: "customers came back less often" leads to contacting the people
    # who have not been in for a while.
    _set(OLD_KPIS + RETURNING, [*OLD_ADDRESSES, {"kind": "figure_moved"}])


def downgrade() -> None:
    _set(OLD_KPIS, OLD_ADDRESSES)
