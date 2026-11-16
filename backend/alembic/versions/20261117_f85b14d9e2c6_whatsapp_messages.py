"""WhatsApp: customer messages (when, which way, who with: never what was said) and the figures
about them (messages, new conversations, how fast they are answered)

Revision ID: f85b14d9e2c6
Revises: e74a03c8d1b5
Create Date: 2026-11-17
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f85b14d9e2c6"
down_revision: str | None = "e74a03c8d1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose records say where they came from, before customer_messages.
TABLES = (
    "customers", "suppliers", "products", "sales", "expenses", "stock_movements",
    "bookings", "opportunities", "projects", "web_visits", "bank_transactions", "ad_spend",
    "staff_hours",
)  # fmt: skip
OLD = (
    "manual", "csv", "excel", "xero", "quickbooks", "square", "zoho", "truelayer", "shopify",
    "woocommerce", "google_analytics", "meta", "hubspot",
)  # fmt: skip
NEW = (*OLD, "whatsapp")
KINDS_OLD = OLD[1:]
KINDS_NEW = (*KINDS_OLD, "whatsapp")

# Frozen on purpose (see the first KPI seed). Formulas use the measures in app/kpi/measures.py.
# (code, name, description, unit, expression, direction)
KPIS = [
    ("messages_received", "Messages from customers",
     "How many WhatsApp messages customers sent you.",
     "count", "messages_received", "up_good"),
    ("conversations_started", "New conversations",
     "How many times a customer got in touch after at least a day of quiet.",
     "count", "conversations_started", "up_good"),
    ("replied_within_hour_pct", "Replied to within an hour",
     "Of the new conversations, how many got a reply within an hour.",
     "percent", "replied_within_hour / answerable_starts * 100", "up_good"),
    ("average_reply_minutes", "Wait for a first reply (minutes)",
     "How long, on average, a customer waited for your first reply. Conversations that got no "
     "reply are not counted here.",
     "count", "reply_minutes / replied_starts", "down_good"),
    ("unanswered_conversations", "Conversations with no reply",
     "New conversations that had no reply from you within a day.",
     "count", "unanswered_starts", "down_good"),
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
        "customer_messages",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("contact_ref", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=20), server_default="whatsapp", nullable=False),
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
        sa.CheckConstraint(_rule("source", NEW), name=op.f("ck_customer_messages_source_valid")),
        sa.CheckConstraint(
            _rule("direction", ("in", "out")), name=op.f("ck_customer_messages_direction_valid")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "import_id"],
            ["data_imports.organization_id", "data_imports.id"],
            name=op.f("fk_customer_messages_organization_id_data_imports"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_customer_messages_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_messages")),
        sa.UniqueConstraint("organization_id", "id", name="uq_customer_messages_org_id"),
    )
    op.create_index(
        "ix_customer_messages_org_sent",
        "customer_messages",
        ["organization_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "ix_customer_messages_org_contact",
        "customer_messages",
        ["organization_id", "contact_ref", "sent_at"],
        unique=False,
    )
    op.create_index(
        "ix_customer_messages_org_import",
        "customer_messages",
        ["organization_id", "import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_messages_organization_id"),
        "customer_messages",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "uq_customer_messages_source_ref",
        "customer_messages",
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
                "category": "operational",
                "unit": unit,
                "expression": expression,
                "direction": direction,
                "requires": ["messages"],
                "sort_order": 1200 + index * 10,
            }
            for index, (code, name, description, unit, expression, direction) in enumerate(KPIS)
        ],
    )


def _forget_what_was_worked_out_from_the_figures(codes: str) -> None:
    """Changes spotted, forecasts, explanations and actions that point at these figures go with
    them (they are worked out again from the data), whichever tables hold them."""
    refs = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT c.conrelid::regclass::text, a.attname, fa.attname
                FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                JOIN pg_attribute fa ON fa.attrelid = c.confrelid AND fa.attnum = c.confkey[1]
                WHERE c.contype = 'f' AND c.confrelid = 'kpi_definitions'::regclass
                """
            )
        )
        .all()
    )
    for table, column, target in refs:
        op.execute(
            f"DELETE FROM {table} WHERE {column} IN "
            f"(SELECT {target} FROM kpi_definitions WHERE code IN ({codes}))"
        )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in CODES)
    _forget_what_was_worked_out_from_the_figures(codes)
    op.execute(f"DELETE FROM kpi_definitions WHERE code IN ({codes})")
    op.drop_index(
        "uq_customer_messages_source_ref",
        table_name="customer_messages",
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.drop_index(op.f("ix_customer_messages_organization_id"), table_name="customer_messages")
    op.drop_index("ix_customer_messages_org_import", table_name="customer_messages")
    op.drop_index("ix_customer_messages_org_contact", table_name="customer_messages")
    op.drop_index("ix_customer_messages_org_sent", table_name="customer_messages")
    op.drop_table("customer_messages")
    _swap(OLD, KINDS_OLD)
