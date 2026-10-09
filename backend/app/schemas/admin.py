import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Reason = Annotated[str, Field(min_length=5, max_length=500)]


class StaffMeOut(BaseModel):
    is_staff: bool
    role: Literal["support", "admin"] | None


class AdminOrgRow(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    member_count: int
    plan_name: str | None  # none until the business has looked at its plan
    plan_status: str | None


class AdminOrgPage(BaseModel):
    total: int
    items: list[AdminOrgRow]


class AdminMember(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    last_login_at: datetime | None
    user_active: bool


class AdminUsage(BaseModel):
    members: int
    integrations: int
    scheduled_reports: int


class AdminSubscription(BaseModel):
    plan_code: str
    plan_name: str
    status: str  # as stored
    interval: str
    provider: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool


class AdminOrgDetail(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    created_by_email: str | None
    members: list[AdminMember]
    subscription: AdminSubscription | None
    usage: AdminUsage
    last_activity_at: datetime | None


class AdminUserRow(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    email_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    organization_count: int
    staff_role: str | None


class AdminUserPage(BaseModel):
    total: int
    items: list[AdminUserRow]


class AdminMembership(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_status: str
    role: str
    status: str


class AdminUserDetail(AdminUserRow):
    memberships: list[AdminMembership]
    active_sessions: int


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class RoleChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "manager", "viewer"]
    reason: Reason


class ExtendTrialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: Annotated[int, Field(ge=1, le=90)]
    reason: Reason


class AdminEntry(BaseModel):
    id: uuid.UUID
    action: str
    created_at: datetime
    actor_email: str | None
    organization_id: uuid.UUID | None
    organization_name: str | None
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    details: dict | None


class AdminAuditPage(BaseModel):
    entries: list[AdminEntry]
    next_before: uuid.UUID | None


class AdminPlanFeature(BaseModel):
    feature: str
    enabled: bool
    limit: int | None


class AdminPlan(BaseModel):
    code: str
    name: str
    price_month_pence: int | None
    price_year_pence: int | None
    currency: str
    self_serve: bool
    is_public: bool
    is_active: bool
    provider_prices: dict
    features: list[AdminPlanFeature]


class PlanChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_month_pence: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    price_year_pence: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    is_public: bool | None = None
    is_active: bool | None = None
    reason: Reason


class EntitlementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    limit: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    reason: Reason
