"""invoices can be tracked: a sale can have a due day and a day it was paid

Revision ID: 88248b978c6f
Revises: e9b4c7d25a61
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "88248b978c6f"
down_revision: str | None = "e9b4c7d25a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("due_on", sa.Date(), nullable=True))
    op.add_column("sales", sa.Column("paid_on", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_sales_due_not_before_sale"), "sales", "due_on IS NULL OR due_on >= sold_on"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sales_due_not_before_sale"), "sales", type_="check")
    op.drop_column("sales", "paid_on")
    op.drop_column("sales", "due_on")
