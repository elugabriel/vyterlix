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


class BreakdownRowOut(BaseModel):
    key: uuid.UUID | None  # None for "no channel recorded" and "everything else"
    label: str
    revenue: str  # net of VAT, after refunds, as pounds
    share_pct: str | None  # of all sales in the period; None when there were none
    count: int  # sales (by channel) or items sold (by product)
    gross_profit: str | None  # revenue minus cost of goods; by product only


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
