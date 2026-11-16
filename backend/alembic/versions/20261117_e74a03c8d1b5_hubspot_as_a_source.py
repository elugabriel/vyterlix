"""HubSpot as a source: its deals become opportunities and its customers become customers

Revision ID: e74a03c8d1b5
Revises: d63f92b1e8a4
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e74a03c8d1b5"
down_revision: str | None = "d63f92b1e8a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose records say where they came from (a model's `_origin_rules`).
TABLES = (
    "customers", "suppliers", "products", "sales", "expenses", "stock_movements",
    "bookings", "opportunities", "projects", "web_visits", "bank_transactions", "ad_spend",
    "staff_hours",
)  # fmt: skip
OLD = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "zoho", "truelayer", "shopify",
    "woocommerce", "google_analytics", "meta",
)  # fmt: skip
NEW = (*OLD, "hubspot")
KINDS_OLD = OLD[1:]
KINDS_NEW = (*KINDS_OLD, "hubspot")


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
    op.execute("DELETE FROM opportunities WHERE source = 'hubspot'")
    _swap(OLD, KINDS_OLD)
