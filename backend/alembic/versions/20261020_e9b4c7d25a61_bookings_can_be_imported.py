"""bookings can be imported from a file

Revision ID: e9b4c7d25a61
Revises: d7a3e5b18f42
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9b4c7d25a61"
down_revision: str | None = "d7a3e5b18f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("data_sources", "data_imports", "data_quality_issues")
OLD = ("sales", "expenses", "customers", "suppliers", "products", "stock_movements")
NEW = (*OLD, "bookings")


def _rule(datasets: tuple[str, ...]) -> str:
    return "dataset IN (" + ", ".join(f"'{d}'" for d in datasets) + ")"


def _swap(datasets: tuple[str, ...]) -> None:
    for table in TABLES:
        name = op.f(f"ck_{table}_dataset_valid")
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _rule(datasets))


def upgrade() -> None:
    _swap(NEW)


def downgrade() -> None:
    _swap(OLD)
