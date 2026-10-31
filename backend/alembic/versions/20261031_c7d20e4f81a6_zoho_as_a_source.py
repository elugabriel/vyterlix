"""Zoho can be the source of records

Revision ID: c7d20e4f81a6
Revises: a41e7c52b9d3
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7d20e4f81a6"
down_revision: str | None = "a41e7c52b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose records say where they came from (a model's `_origin_rules`).
TABLES = (
    "customers", "suppliers", "products", "sales", "expenses", "stock_movements",
    "bookings", "opportunities", "projects",
)  # fmt: skip
OLD = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "shopify", "woocommerce",
    "google_analytics",
)  # fmt: skip
NEW = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "zoho", "shopify", "woocommerce",
    "google_analytics",
)  # fmt: skip
KINDS_OLD = (
    "csv", "excel", "xero", "quickbooks", "square", "shopify", "woocommerce", "google_analytics",
)  # fmt: skip
KINDS_NEW = (
    "csv", "excel", "xero", "quickbooks", "square", "zoho", "shopify", "woocommerce",
    "google_analytics",
)  # fmt: skip


def _rule(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _swap(sources: tuple[str, ...], kinds: tuple[str, ...]) -> None:
    for table in TABLES:
        name = op.f(f"ck_{table}_source_valid")
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _rule("source", sources))
    name = op.f("ck_data_sources_kind_valid")
    op.drop_constraint(name, "data_sources", type_="check")
    op.create_check_constraint(name, "data_sources", _rule("kind", kinds))


def upgrade() -> None:
    _swap(NEW, KINDS_NEW)


def downgrade() -> None:
    _swap(OLD, KINDS_OLD)
