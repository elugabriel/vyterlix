import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Interval = Literal["month", "year"]
ProviderKey = Literal["sandbox", "stripe", "paystack"]
Feature = Literal["members", "integrations", "scheduled_reports", "ai_assistant"]


class FeatureOut(BaseModel):
    feature: Feature
    label: str
    enabled: bool
    limit: int | None  # none: no limit
    used: int | None = None  # how much the business has now (where something is counted)
    text: str  # "Up to 10 team members" / "Included" / "Not included"


class PlanOut(BaseModel):
    code: str
    name: str
    description: str
    self_serve: bool  # can be bought here (Corporate is by talking to us)
    price_month: str | None  # excluding VAT, ready to show: "£99.00"
    price_year: str | None
    price_month_pence: int | None
    price_year_pence: int | None
    year_saving: str | None
    vat_note: str
    features: list[FeatureOut]
    current: bool


class SubscriptionOut(BaseModel):
    plan_code: str
    plan_name: str
    status: Literal["trialing", "active", "past_due", "canceled", "expired"]
    status_label: str
    interval: Interval
    provider: str
    trial_ends_at: datetime | None
    trial_days_left: int | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    scheduled_plan_code: str | None
    scheduled_plan_name: str | None
    message: str  # what this means, in plain words


class BillingOut(BaseModel):
    subscription: SubscriptionOut
    features: list[FeatureOut]  # what the business has now, with what it uses
    providers: list[ProviderKey]  # the ways it can pay here
    can_manage: bool  # the owner can change the plan


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: Annotated[str, Field(min_length=1, max_length=30)]
    interval: Interval = "month"
    provider: ProviderKey | None = None  # the default way of paying if left out


class CheckoutOut(BaseModel):
    url: str  # where to go to pay


class SandboxCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, Field(min_length=10, max_length=2000)]


class ChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: Annotated[str, Field(min_length=1, max_length=30)]
    interval: Interval | None = None  # keep the present one if left out


class ChangeOut(BaseModel):
    result: Literal["changed_now", "scheduled", "checkout"]
    message: str
    checkout_url: str | None = None  # for a provider that changes a plan by a new checkout
    billing: BillingOut


class InvoiceOut(BaseModel):
    id: uuid.UUID
    number: str | None
    status: Literal["draft", "open", "paid", "void", "uncollectible"]
    status_label: str
    currency: str
    net: str
    vat: str
    total: str
    period_start: date | None
    period_end: date | None
    issued_at: datetime
    paid_at: datetime | None
    hosted_url: str | None
    lines: list[dict]


class WebhookOut(BaseModel):
    received: int
    processed: int
    ignored: int
    duplicates: int
    unmatched: int
    failed: int
