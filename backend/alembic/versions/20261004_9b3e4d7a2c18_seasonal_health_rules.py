"""health rules can follow the trading year (seasonal)

Revision ID: 9b3e4d7a2c18
Revises: f2d8c6b1a937
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3e4d7a2c18"
down_revision: str | None = "f2d8c6b1a937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The figures that rise and fall with the trading year. Margins, order size and stock turnover
# do not, so they keep being compared with the plain recent average.
SEASONAL = ("revenue", "net_profit", "active_customers")


def upgrade() -> None:
    op.add_column(
        "health_rules",
        sa.Column("seasonal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    rules = sa.table(
        "health_rules", sa.column("kpi_code", sa.String), sa.column("seasonal", sa.Boolean)
    )
    op.execute(rules.update().where(rules.c.kpi_code.in_(SEASONAL)).values(seasonal=True))


def downgrade() -> None:
    op.drop_column("health_rules", "seasonal")
