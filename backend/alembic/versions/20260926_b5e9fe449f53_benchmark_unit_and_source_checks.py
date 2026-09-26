"""benchmark unit and source checks

Revision ID: b5e9fe449f53
Revises: 296030419acc
Create Date: 2026-09-26 12:19:26.546976
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e9fe449f53"
down_revision: str | None = "296030419acc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hand-written: autogenerate doesn't detect new CHECK constraints.
    op.create_check_constraint(
        "unit_valid",
        "business_benchmarks",
        "unit IN ('gbp', 'percent', 'count', 'ratio', 'days')",
    )
    op.create_check_constraint("source_required", "business_benchmarks", "length(trim(source)) > 0")


def downgrade() -> None:
    op.drop_constraint("ck_business_benchmarks_source_required", "business_benchmarks")
    op.drop_constraint("ck_business_benchmarks_unit_valid", "business_benchmarks")
