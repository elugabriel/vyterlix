import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = StringConstraints(strip_whitespace=True)
Reason = Annotated[str, Text, Field(min_length=5, max_length=500)]


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
    open_cases: int = 0
    notes: list["AdminNoteOut"] = []


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
    notes: list["AdminNoteOut"] = []


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


# --- notes, cases, flags, system events ----------------------------------------------

Body = Annotated[str, Text, Field(min_length=1, max_length=4000)]


class AdminNoteOut(BaseModel):
    id: uuid.UUID
    body: str
    author_email: str | None
    created_at: datetime


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Body
    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class CaseNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Body


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Annotated[str, Text, Field(min_length=3, max_length=200)]
    organization_id: uuid.UUID | None = None
    requester_email: Annotated[str, Field(max_length=320)] | None = None
    priority: Literal["low", "normal", "high"] = "normal"
    note: Body | None = None


class CaseChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Annotated[str, Text, Field(min_length=3, max_length=200)] | None = None
    status: Literal["open", "waiting", "resolved"] | None = None
    priority: Literal["low", "normal", "high"] | None = None
    assigned_to_email: Annotated[str, Field(max_length=320)] | None = None
    unassign: bool = False


class CaseRow(BaseModel):
    id: uuid.UUID
    subject: str
    status: str
    priority: str
    organization_id: uuid.UUID | None
    organization_name: str | None
    requester_email: str | None
    assigned_to_email: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class CasePage(BaseModel):
    total: int
    items: list[CaseRow]


class CaseDetail(CaseRow):
    created_by_email: str | None
    notes: list[AdminNoteOut]


class FlagOverride(BaseModel):
    organization_id: uuid.UUID
    organization_name: str | None
    enabled: bool


class FlagOut(BaseModel):
    key: str
    description: str
    enabled: bool
    overrides: list[FlagOverride]


class FlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")]
    description: Annotated[str, Text, Field(min_length=3, max_length=300)]
    enabled: bool = False
    reason: Reason


class FlagChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Annotated[str, Text, Field(min_length=3, max_length=300)] | None = None
    enabled: bool | None = None
    reason: Reason


class FlagOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reason: Reason


class FeaturesOut(BaseModel):
    flags: dict[str, bool]


class SystemEventOut(BaseModel):
    id: uuid.UUID
    kind: str
    severity: str
    message: str
    details: dict | None
    organization_id: uuid.UUID | None
    organization_name: str | None
    created_at: datetime
    resolved_at: datetime | None


class SystemEventPage(BaseModel):
    items: list[SystemEventOut]
    next_before: uuid.UUID | None


class HealthJobs(BaseModel):
    queued: int
    running: int
    stuck: int  # running but not heard from for a while
    failed_last_day: int
    oldest_waiting_seconds: int | None


class HealthEmails(BaseModel):
    waiting: int
    failed_last_day: int


class HealthIntegrations(BaseModel):
    connected: int
    needing_attention: int


class HealthAccounts(BaseModel):
    businesses: dict[str, int]  # by status
    people: int
    locked_people: int
    subscriptions: dict[str, int]  # by status, as stored


class HealthOut(BaseModel):
    status: Literal["ok", "attention"]
    checked_at: datetime
    database_ok: bool
    migration: str | None
    environment: str
    email_backend: str
    ai_provider: str
    jobs: HealthJobs
    emails: HealthEmails
    integrations: HealthIntegrations
    accounts: HealthAccounts
    open_errors: int
    open_warnings: int
    open_cases: int


AdminOrgDetail.model_rebuild()
AdminUserDetail.model_rebuild()
