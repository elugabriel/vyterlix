"""Facebook and Instagram adverts: Meta as a source, daily advert spend, and the marketing figures

Revision ID: d81f6a3c5e90
Revises: c4a7e19b3d52
Create Date: 2026-11-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d81f6a3c5e90"
down_revision: str | None = "c4a7e19b3d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose records say where they came from (a model's `_origin_rules`), before ad_spend.
TABLES = (
    "customers", "suppliers", "products", "sales", "expenses", "stock_movements",
    "bookings", "opportunities", "projects", "web_visits", "bank_transactions",
)  # fmt: skip
OLD = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "zoho", "truelayer", "shopify",
    "woocommerce", "google_analytics",
)  # fmt: skip
NEW = (*OLD, "meta")
KINDS_OLD = OLD[1:]
KINDS_NEW = (*KINDS_OLD, "meta")

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, unit, expression, direction, requires)
KPIS = [
    ("ad_spend", "Spent on adverts", "What you spent on Facebook and Instagram adverts.",
     "gbp", "ad_spend", "neutral", ["ads"]),
    ("ad_clicks", "Clicks on your adverts", "How many times people clicked on your adverts.",
     "count", "ad_clicks", "up_good", ["ads"]),
    ("ad_cost_per_click", "Cost of a click", "What you paid, on average, for one click on an advert.",
     "gbp", "ad_spend / ad_clicks", "down_good", ["ads"]),
    ("ad_results", "Enquiries and orders from adverts",
     "How many enquiries, messages and orders your adverts brought, as Facebook counts them.",
     "count", "ad_results", "up_good", ["ads"]),
    ("ad_cost_per_result", "Cost of an enquiry or order",
     "What you paid, on average, for each enquiry or order your adverts brought.",
     "gbp", "ad_spend / ad_results", "down_good", ["ads"]),
    ("cost_per_new_customer", "Cost of winning a new customer",
     "What you spent on adverts for each customer who bought from you for the first time. All "
     "your new customers count, not only those who came from an advert.",
     "gbp", "ad_spend / new_customers", "down_good", ["ads", "customer_sales"]),
    ("sales_per_ad_pound", "Sales for each £1 spent on adverts",
     "Your sales, before VAT, for each £1 spent on adverts. All your sales count, not only those "
     "that came from an advert.",
     "gbp", "revenue / ad_spend", "up_good", ["ads", "sales"]),
]  # fmt: skip
CODES = [k[0] for k in KPIS]


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
    op.create_table(
        "ad_spend",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("spend", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("impressions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("results", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column("import_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(_rule("source", NEW), name=op.f("ck_ad_spend_source_valid")),
        sa.CheckConstraint(
            "spend >= 0 AND impressions >= 0 AND clicks >= 0 AND results >= 0",
            name=op.f("ck_ad_spend_amounts_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["data_imports.organization_id", "data_imports.id"],
            name=op.f("fk_ad_spend_organization_id_data_imports"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_ad_spend_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ad_spend")),
        sa.UniqueConstraint("organization_id", "id", name="uq_ad_spend_org_id"),
    )
    op.create_index("ix_ad_spend_org_day", "ad_spend", ["organization_id", "day"], unique=False)
    op.create_index(
        "ix_ad_spend_org_import", "ad_spend", ["organization_id", "import_id"], unique=False
    )
    op.create_index(
        op.f("ix_ad_spend_organization_id"), "ad_spend", ["organization_id"], unique=False
    )
    op.create_index(
        "uq_ad_spend_source_ref",
        "ad_spend",
        ["organization_id", "source", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    table = sa.table(
        "kpi_definitions",
        sa.column("id", sa.Uuid),
        sa.column("code"),
        sa.column("name"),
        sa.column("description"),
        sa.column("category"),
        sa.column("unit"),
        sa.column("expression"),
        sa.column("direction"),
        sa.column("requires", sa.JSON),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "description": description,
                "category": "marketing",
                "unit": unit,
                "expression": expression,
                "direction": direction,
                "requires": requires,
                "sort_order": 900 + index * 10,
            }
            for index, (
                code,
                name,
                description,
                unit,
                expression,
                direction,
                requires,
            ) in enumerate(KPIS)
        ],
    )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in CODES)
    op.execute(
        f"DELETE FROM kpi_values WHERE kpi_id IN (SELECT id FROM kpi_definitions WHERE code IN ({codes}))"
    )
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
    op.drop_index(
        "uq_ad_spend_source_ref",
        table_name="ad_spend",
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.drop_index(op.f("ix_ad_spend_organization_id"), table_name="ad_spend")
    op.drop_index("ix_ad_spend_org_import", table_name="ad_spend")
    op.drop_index("ix_ad_spend_org_day", table_name="ad_spend")
    op.drop_table("ad_spend")
    _swap(OLD, KINDS_OLD)
