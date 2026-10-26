import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Granularity = Literal["week", "month", "quarter", "year"]


class KpiValueOut(BaseModel):
    """One KPI for one period. `value` is a decimal string rounded for its unit (pounds and
    percentages to 2 places), or null when it can't be worked out (see `status`)."""

    period_start: date
    period_end: date
    is_complete: bool  # False while the period is still running
    status: Literal["ok", "no_data", "undefined"]
    value: str | None
    previous_value: str | None
    change_pct: str | None
    data_quality: int | None  # 0-100: how complete the data behind this period is
    # Why it is that number: the measures it was worked out from.
    inputs: dict[str, str]


class PlainOut(BaseModel):
    """What a figure means, in everyday words (see app/plain/figures.py)."""

    says: str  # what happened, one sentence
    why: str  # why it matters
    tone: Literal["good", "bad", "watch", "neutral", "unknown"]
    caution: str | None = None  # set when the data behind it is incomplete


class RunOut(BaseModel):
    id: uuid.UUID
    status: Literal["running", "succeeded", "failed"]
    trigger: str
    granularity: str
    period_from: date
    period_to: date
    kpi_count: int
    values_written: int
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


class KpiOut(BaseModel):
    code: str
    name: str
    description: str
    category: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    direction: Literal["up_good", "down_good", "neutral"]
    requires: list[str]  # kinds of record it needs: sales, expenses, customer_sales, stock
    latest: KpiValueOut | None  # the most recent finished period
    current: KpiValueOut | None  # the period in progress, if it has one
    plain: PlainOut | None = None  # what `latest` means, in everyday words


class KpisOut(BaseModel):
    granularity: Granularity
    kpis: list[KpiOut]
    last_run: RunOut | None  # None until the engine has run for this business


class KpiHistoryOut(BaseModel):
    code: str
    name: str
    description: str
    category: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    direction: Literal["up_good", "down_good", "neutral"]
    requires: list[str]
    granularity: Granularity
    formula: str  # the stored formula, so the number can be explained
    values: list[KpiValueOut]  # oldest first
    plain: PlainOut | None = None  # what the newest finished value means, in everyday words


class BreakdownRowOut(BaseModel):
    key: uuid.UUID | None  # None for "no channel recorded" and "everything else"
    label: str
    revenue: str  # net of VAT, after refunds, as pounds
    share_pct: str | None  # of all sales in the period; None when there were none
    count: int  # sales (by channel) or items sold (by product)
    gross_profit: str | None  # revenue minus cost of goods; by product only


class CustomerToFollowUpOut(BaseModel):
    customer_id: uuid.UUID
    name: str
    last_bought_on: date
    days_since: int  # up to the latest sale the business has recorded
    orders: int
    total_spent: str  # before VAT, after refunds


class FollowUpOut(BaseModel):
    """Customers who used to buy and have gone quiet, the best ones first."""

    as_of: date | None  # the latest sale recorded: "quiet" is counted back from it
    quiet_days: int
    total: int  # how many customers have gone quiet (only the best few are listed)
    customers: list[CustomerToFollowUpOut]


class BreakdownOut(BaseModel):
    dimension: Literal["channel", "product"]
    period_from: date
    period_to: date
    total_revenue: str
    rows: list[BreakdownRowOut]  # biggest first; the rest are rolled into "Everything else"


class MoverOut(BaseModel):
    product_id: uuid.UUID
    name: str
    sku: str | None
    units_sold: str  # in the window, after refunds
    on_hand: str  # items in stock now
    stock_value: str | None  # at cost; None when the product has no cost price
    last_sold_on: date | None
    days_since_last_sale: int | None


class MoversOut(BaseModel):
    as_of: date
    days: int
    fast: list[MoverOut]  # selling most
    slow: list[MoverOut]  # selling, but least
    dead: list[MoverOut]  # in stock but not sold in the window


class ServiceRowOut(BaseModel):
    service: str
    kept: int  # appointments that went ahead
    cancelled: int
    missed: int  # did not turn up
    share_pct: str | None  # of all appointments kept in the window
    customers: int  # different customers who kept one
    rebooked_pct: str | None  # of those, the share with another appointment after their last


class ServicesOut(BaseModel):
    """The services people book, most popular first, and where people do not come back."""

    period_from: date | None
    period_to: date | None
    kept: int
    rebooked_pct: str | None  # across every service: the yardstick for "weak"
    rows: list[ServiceRowOut]
    notes: list[str]  # plain sentences: the most popular, and any with weak rebooking


class TeamMemberOut(BaseModel):
    staff: str
    kept: int
    cancelled: int
    missed: int
    share_pct: str | None  # of every appointment kept in the window
    average_rating: str | None  # out of 5, from the appointments that were rated


class TeamOut(BaseModel):
    """Who is carrying the appointments: how many each person did, and how they went."""

    period_from: date | None
    period_to: date | None
    kept: int
    members: list[TeamMemberOut]
    notes: list[str]


class ProjectProfitOut(BaseModel):
    project_id: uuid.UUID
    name: str
    status: str
    client: str | None
    revenue: str  # sales on the project, before VAT, after refunds
    costs: str  # costs on the project, before VAT, after credits
    profit: str
    margin_pct: str | None  # profit as a share of revenue
    budget: str | None
    budget_used_pct: str | None  # costs as a share of the budget


class ProjectsOut(BaseModel):
    """What each project has earned and cost, the ones losing money first."""

    total_revenue: str
    total_costs: str
    unallocated_costs: str  # costs not on any project
    rows: list[ProjectProfitOut]
    notes: list[str]


class BookingChoicesOut(BaseModel):
    """The services and staff already used, so they can be picked rather than retyped."""

    services: list[str]
    staff: list[str]
