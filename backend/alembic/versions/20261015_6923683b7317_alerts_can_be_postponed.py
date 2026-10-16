"""alerts can be postponed

Revision ID: 6923683b7317
Revises: d228cdd9e926
Create Date: 2026-10-15 21:21:33.092455
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6923683b7317"
down_revision: str | None = "d228cdd9e926"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_KINDS = "kind IN ('raised', 'repeated', 'acknowledged', 'resolved', 'reopened')"
NEW_KINDS = (
    "kind IN ('raised', 'repeated', 'acknowledged', 'resolved', 'reopened', 'postponed', "
    "'brought_back')"
)


def upgrade() -> None:
    op.add_column("alerts", sa.Column("snoozed_until", sa.Date(), nullable=True))
    op.drop_constraint(op.f("ck_alert_events_kind_valid"), "alert_events", type_="check")
    op.create_check_constraint(op.f("ck_alert_events_kind_valid"), "alert_events", NEW_KINDS)


def downgrade() -> None:
    op.execute("DELETE FROM alert_events WHERE kind IN ('postponed', 'brought_back')")
    op.drop_constraint(op.f("ck_alert_events_kind_valid"), "alert_events", type_="check")
    op.create_check_constraint(op.f("ck_alert_events_kind_valid"), "alert_events", OLD_KINDS)
    op.drop_column("alerts", "snoozed_until")
