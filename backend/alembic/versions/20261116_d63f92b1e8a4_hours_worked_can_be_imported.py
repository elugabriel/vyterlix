"""hours worked can be imported from a file (and undone like any other import)

Revision ID: d63f92b1e8a4
Revises: c52e81f0a7d3
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d63f92b1e8a4"
down_revision: str | None = "c52e81f0a7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("data_sources", "data_imports", "data_quality_issues")
OLD = (
    "sales", "expenses", "customers", "suppliers", "products", "stock_movements", "bookings",
    "opportunities",
)  # fmt: skip
NEW = (*OLD, "staff_hours")
SOURCES = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "zoho", "truelayer", "shopify",
    "woocommerce", "google_analytics", "meta",
)  # fmt: skip


def _rule(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _swap(datasets: tuple[str, ...]) -> None:
    for table in TABLES:
        name = op.f(f"ck_{table}_dataset_valid")
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _rule("dataset", datasets))


def upgrade() -> None:
    _swap(NEW)
    op.add_column(
        "staff_hours",
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
    )
    op.add_column("staff_hours", sa.Column("source_ref", sa.String(length=200), nullable=True))
    op.add_column("staff_hours", sa.Column("import_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_staff_hours_source_valid"), "staff_hours", _rule("source", SOURCES)
    )
    op.create_unique_constraint("uq_staff_hours_org_id", "staff_hours", ["organization_id", "id"])
    op.create_foreign_key(
        op.f("fk_staff_hours_organization_id_data_imports"),
        "staff_hours",
        "data_imports",
        ["organization_id", "import_id"],
        ["organization_id", "id"],
    )
    op.create_index(
        "ix_staff_hours_org_import", "staff_hours", ["organization_id", "import_id"], unique=False
    )
    op.create_index(
        "uq_staff_hours_source_ref",
        "staff_hours",
        ["organization_id", "source", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_hours WHERE source <> 'manual'")
    op.drop_index(
        "uq_staff_hours_source_ref",
        table_name="staff_hours",
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.drop_index("ix_staff_hours_org_import", table_name="staff_hours")
    op.drop_constraint(
        op.f("fk_staff_hours_organization_id_data_imports"), "staff_hours", type_="foreignkey"
    )
    op.drop_constraint("uq_staff_hours_org_id", "staff_hours", type_="unique")
    op.drop_constraint(op.f("ck_staff_hours_source_valid"), "staff_hours", type_="check")
    op.drop_column("staff_hours", "import_id")
    op.drop_column("staff_hours", "source_ref")
    op.drop_column("staff_hours", "source")
    for table in TABLES:
        op.execute(f"DELETE FROM {table} WHERE dataset = 'staff_hours'")
    _swap(OLD)
