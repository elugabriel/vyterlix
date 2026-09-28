"""A business's own trading data (Phase 4): customers, suppliers, products, sales,
expenses and stock movements. This is what every KPI, forecast and diagnosis reads.

Rules, enforced by the database:
- Every table is tenant data (TenantScopedMixin), so queries are scoped automatically.
- Links between records are composite (organization_id, id) foreign keys: a sale in
  business A can never point at a customer, product or channel of business B, even if
  application code had a bug. Referenced records can't be deleted while in use
  (archive instead; undoing an import deletes in dependency order).
- Money is NUMERIC in GBP, stored excluding VAT with the VAT alongside (ADR 0001 §4), and
  gross = net + VAT exactly. Refunds and credits are negative rows of their own kind.
- Every row records where it came from (source + source_ref), so the same sale or
  expense can't be imported twice from the same source.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.business import UK_POSTCODE_REGEX
from app.models.identity import _one_of

# Where a record came from. "manual" = typed in; the connectors arrive in Phase 4 Part B.
DATA_SOURCES = ("manual", "csv", "excel", "xero", "shopify", "woocommerce", "google_analytics")
UK_VAT_RATES = ("0", "5", "20")  # %, as NUMERIC text; NULL = exempt / outside the scope of VAT
SALE_KINDS = ("sale", "refund")
EXPENSE_KINDS = ("expense", "credit")
STOCK_MOVEMENT_KINDS = ("opening", "delivery", "sale", "return", "adjustment", "write_off")

Money = Numeric(18, 4)
Quantity = Numeric(18, 4)


def _tenant_fk(column: str, target: str, *, ondelete: str | None = None) -> ForeignKeyConstraint:
    """(organization_id, column) -> target(organization_id, id): same-business links only."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        ondelete=ondelete,
    )


class DataOriginMixin:
    """Where a record came from, and its id there (used to stop double imports)."""

    source: Mapped[str] = mapped_column(String(20), server_default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(200))


def _origin_rules(table: str) -> tuple:
    return (
        CheckConstraint(_one_of("source", DATA_SOURCES), name="source_valid"),
        # The same record from the same source can only be stored once per business.
        Index(
            f"uq_{table}_source_ref",
            "organization_id",
            "source",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
        # Composite target for same-business links from other tables.
        UniqueConstraint("organization_id", "id", name=f"uq_{table}_org_id"),
    )


def _amounts_rules() -> tuple:
    return (
        CheckConstraint("currency = 'GBP'", name="currency_gbp"),
        CheckConstraint("gross_amount = net_amount + vat_amount", name="gross_is_net_plus_vat"),
    )


class Customer(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    """Someone who buys. Personal details are optional: keep only what's needed (UK GDPR)."""

    __tablename__ = "customers"
    __table_args__ = (
        *_origin_rules("customers"),
        _tenant_fk("customer_type_id", "business_list_items"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(f"postcode ~ '{UK_POSTCODE_REGEX}'", name="postcode_format"),
    )

    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    postcode: Mapped[str | None] = mapped_column(String(8))
    customer_type_id: Mapped[uuid.UUID | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Supplier(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        *_origin_rules("suppliers"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Product(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        *_origin_rules("products"),
        _tenant_fk("offering_id", "business_list_items"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("unit_price_ex_vat >= 0", name="unit_price_not_negative"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_not_negative"),
        CheckConstraint(_one_of("vat_rate", UK_VAT_RATES), name="vat_rate_uk"),
        # A SKU identifies one product within a business.
        Index(
            "uq_products_sku",
            "organization_id",
            "sku",
            unique=True,
            postgresql_where=text("sku IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(100))
    offering_id: Mapped[uuid.UUID | None] = mapped_column()
    unit_price_ex_vat: Mapped[Decimal | None] = mapped_column(Money)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Sale(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    """One sale, order or refund. Amounts are totals for the whole sale."""

    __tablename__ = "sales"
    __table_args__ = (
        *_origin_rules("sales"),
        *_amounts_rules(),
        _tenant_fk("customer_id", "customers"),
        _tenant_fk("sales_channel_id", "business_list_items"),
        CheckConstraint(_one_of("kind", SALE_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind = 'sale' AND net_amount >= 0 AND vat_amount >= 0)"
            " OR (kind = 'refund' AND net_amount <= 0 AND vat_amount <= 0)",
            name="sign_matches_kind",
        ),
        CheckConstraint("discount_amount >= 0", name="discount_not_negative"),
        Index("ix_sales_org_date", "organization_id", "sold_on"),
    )

    kind: Mapped[str] = mapped_column(String(10), server_default="sale")
    sold_on: Mapped[date] = mapped_column(Date)  # the business's own (UK) trading date
    customer_id: Mapped[uuid.UUID | None] = mapped_column()
    sales_channel_id: Mapped[uuid.UUID | None] = mapped_column()
    net_amount: Mapped[Decimal] = mapped_column(Money)  # excluding VAT, after discounts
    vat_amount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    gross_amount: Mapped[Decimal] = mapped_column(Money)  # what the customer paid
    discount_amount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), server_default="GBP")
    notes: Mapped[str | None] = mapped_column(Text)


class SaleLine(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One product line of a sale. cost_amount is the cost of goods sold for the line,
    blank when unknown (reported as a data-quality gap, never guessed)."""

    __tablename__ = "sale_lines"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sale_lines_org_id"),
        _tenant_fk("sale_id", "sales", ondelete="CASCADE"),
        _tenant_fk("product_id", "products"),
        CheckConstraint("quantity <> 0", name="quantity_not_zero"),
        CheckConstraint("unit_price_ex_vat >= 0", name="unit_price_not_negative"),
        CheckConstraint("discount_amount >= 0", name="discount_not_negative"),
        CheckConstraint("cost_amount >= 0", name="cost_not_negative"),
        CheckConstraint(_one_of("vat_rate", UK_VAT_RATES), name="vat_rate_uk"),
        Index("ix_sale_lines_org_sale", "organization_id", "sale_id"),
        Index("ix_sale_lines_org_product", "organization_id", "product_id"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column()
    product_id: Mapped[uuid.UUID | None] = mapped_column()
    description: Mapped[str | None] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Quantity)  # negative on refunds
    unit_price_ex_vat: Mapped[Decimal | None] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    net_amount: Mapped[Decimal] = mapped_column(Money)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    cost_amount: Mapped[Decimal | None] = mapped_column(Money)


class Expense(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        *_origin_rules("expenses"),
        *_amounts_rules(),
        _tenant_fk("supplier_id", "suppliers"),
        _tenant_fk("cost_category_id", "business_list_items"),
        CheckConstraint(_one_of("kind", EXPENSE_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind = 'expense' AND net_amount >= 0 AND vat_amount >= 0)"
            " OR (kind = 'credit' AND net_amount <= 0 AND vat_amount <= 0)",
            name="sign_matches_kind",
        ),
        Index("ix_expenses_org_date", "organization_id", "spent_on"),
    )

    kind: Mapped[str] = mapped_column(String(10), server_default="expense")
    spent_on: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column()
    cost_category_id: Mapped[uuid.UUID | None] = mapped_column()
    description: Mapped[str | None] = mapped_column(String(300))
    net_amount: Mapped[Decimal] = mapped_column(Money)
    vat_amount: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    gross_amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), server_default="GBP")


class StockMovement(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, DataOriginMixin, Base):
    """Stock in (+) or out (-). Current stock = the sum of a product's movements."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        *_origin_rules("stock_movements"),
        _tenant_fk("product_id", "products"),
        _tenant_fk("sale_line_id", "sale_lines", ondelete="CASCADE"),
        CheckConstraint(_one_of("kind", STOCK_MOVEMENT_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind IN ('opening') AND quantity >= 0)"
            " OR (kind IN ('delivery', 'return') AND quantity > 0)"
            " OR (kind IN ('sale', 'write_off') AND quantity < 0)"
            " OR (kind = 'adjustment' AND quantity <> 0)",
            name="sign_matches_kind",
        ),
        CheckConstraint("unit_cost >= 0", name="unit_cost_not_negative"),
        Index("ix_stock_movements_org_product_date", "organization_id", "product_id", "moved_on"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column()
    moved_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(12))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    sale_line_id: Mapped[uuid.UUID | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(String(300))
