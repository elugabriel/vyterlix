"""Billing (Phase 16): plans, what each plan includes, and each business's subscription.

- plans: what can be bought. The prices are rows that can be changed (never written into the code).
  Every price here to begin with is a placeholder to be confirmed before launch.
- feature_entitlements: what each plan includes: a switch or a limit for each gated feature.
- subscriptions: one per business: its plan, state, and the payment provider's references.
- subscription_items: what a subscription is made of (the plan, and anything extra).
- invoices: what a business has been charged (a copy of what the payment provider issued).
- billing_events: every message the provider sent about a business, so none is acted on twice.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

FEATURES = ("members", "integrations", "scheduled_reports", "ai_assistant")
SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "canceled", "expired")
INTERVALS = ("month", "year")
PROVIDERS = ("none", "sandbox", "stripe", "paystack")
INVOICE_STATUSES = ("draft", "open", "paid", "void", "uncollectible")
EVENT_STATUSES = ("processed", "ignored", "failed")
ITEM_KINDS = ("plan", "extra")


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_plans_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "price_month_pence IS NULL OR price_month_pence >= 0", name="month_price_valid"
        ),
        CheckConstraint(
            "price_year_pence IS NULL OR price_year_pence >= 0", name="year_price_valid"
        ),
        CheckConstraint(
            "NOT self_serve OR price_month_pence IS NOT NULL", name="self_serve_has_a_price"
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    )

    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    price_month_pence: Mapped[int | None] = mapped_column(Integer)  # excluding VAT
    price_year_pence: Mapped[int | None] = mapped_column(Integer)  # excluding VAT
    currency: Mapped[str] = mapped_column(String(3), server_default="GBP")
    self_serve: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )  # can be bought here
    is_public: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )  # shown to businesses
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    # The provider's own ids for this plan: {"stripe": {"month": "price_..", "year": ".."}, ...}
    provider_prices: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class FeatureEntitlement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feature_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_id", "feature", name="uq_feature_entitlements_plan_feature"),
        CheckConstraint(_one_of("feature", FEATURES), name="feature_valid"),
        CheckConstraint('"limit" IS NULL OR "limit" >= 0', name="limit_not_negative"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    feature: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    limit: Mapped[int | None] = mapped_column(Integer)  # none: no limit


class Subscription(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_subscriptions_organization"),
        UniqueConstraint("organization_id", "id", name="uq_subscriptions_org_id"),
        CheckConstraint(_one_of("status", SUBSCRIPTION_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("interval", INTERVALS), name="interval_valid"),
        CheckConstraint(_one_of("provider", PROVIDERS), name="provider_valid"),
        Index(
            "uq_subscriptions_provider_ref",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where=text("provider_subscription_id IS NOT NULL"),
        ),
        Index("ix_subscriptions_customer", "provider", "provider_customer_id"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    scheduled_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plans.id")
    )  # at renewal
    scheduled_interval: Mapped[str | None] = mapped_column(String(5))  # and how it is billed then
    status: Mapped[str] = mapped_column(String(15))
    interval: Mapped[str] = mapped_column(String(5), server_default="month")
    provider: Mapped[str] = mapped_column(String(15), server_default="none")
    provider_customer_id: Mapped[str | None] = mapped_column(String(120))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionItem(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "subscription_items"
    __table_args__ = (
        CheckConstraint(_one_of("kind", ITEM_KINDS), name="kind_valid"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("unit_amount_pence >= 0", name="amount_not_negative"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    unit_amount_pence: Mapped[int] = mapped_column(Integer)  # excluding VAT


class Invoice(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("provider", "provider_invoice_id", name="uq_invoices_provider_ref"),
        CheckConstraint(_one_of("status", INVOICE_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("provider", PROVIDERS), name="provider_valid"),
        CheckConstraint("net_pence >= 0 AND vat_pence >= 0", name="amounts_not_negative"),
        CheckConstraint("total_pence = net_pence + vat_pence", name="total_adds_up"),
        Index("ix_invoices_org_issued", "organization_id", "issued_at"),
    )

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(15))
    provider_invoice_id: Mapped[str] = mapped_column(String(120))
    number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(15))
    currency: Mapped[str] = mapped_column(String(3), server_default="GBP")
    net_pence: Mapped[int] = mapped_column(BigInteger)
    vat_pence: Mapped[int] = mapped_column(BigInteger)
    total_pence: Mapped[int] = mapped_column(BigInteger)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hosted_url: Mapped[str | None] = mapped_column(String(500))  # the provider's page for it
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))


class BillingEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_billing_events_provider_event"),
        CheckConstraint(_one_of("status", EVENT_STATUSES), name="status_valid"),
        Index("ix_billing_events_org", "organization_id", "received_at"),
    )

    provider: Mapped[str] = mapped_column(String(15))
    event_id: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'")
    )  # what was understood
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
