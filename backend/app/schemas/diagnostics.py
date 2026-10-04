import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DetectionOut(BaseModel):
    """Something worth a look in a business's figures, with the numbers behind it."""

    id: uuid.UUID
    kpi_code: str  # open it on the Key figures page
    kpi_name: str
    category: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    kind: Literal["material_change", "anomaly"]
    period_start: date
    period_end: date
    direction: Literal["up", "down"]
    severity: Literal["notable", "major"]
    effect: Literal["good", "bad", "neutral"]  # what it means for the business
    value: str
    reference_value: str  # what it is compared with (the month before)
    change: str  # signed; in per cent, or percentage points when `change_unit` is "points"
    change_unit: Literal["percent", "points"]
    explained_by_season: bool  # about what the owner's busy and quiet seasons lead you to expect
    data_quality: int | None
    summary: str  # a plain sentence on what moved and by how much
    status: Literal["open", "dismissed", "diagnosed"]
    detected_at: datetime


class SegmentRowOut(BaseModel):
    """One part of a figure (a product, a channel...) and how it moved."""

    key: str | None  # null for the "Everything else" row
    label: str
    current: str
    previous: str
    change: str  # current - previous, signed
    # What share of the overall change this part is. Negative: it moved against the overall change.
    share_of_change_pct: str | None
    state: Literal["new", "gone", "up", "down", "steady"]
    text: str  # a plain sentence on this part


class SegmentOut(BaseModel):
    """A figure split into parts, for one month against the month before (or last year)."""

    metric: str  # a KPI code
    metric_label: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    dimension: str
    dimension_label: str
    month: date
    compared_with: date
    against: Literal["previous_month", "last_year"]
    total_current: str
    total_previous: str
    total_change: str
    total_change_pct: str | None
    headline: str  # one sentence on the whole figure
    rows: list[SegmentRowOut]  # biggest movers first; parts that did not move are left out


class SegmentOptionsOut(BaseModel):
    """Which figures can be split up, and by what."""

    metrics: dict[str, list[str]]


class FindingOut(BaseModel):
    """One thing that drove a change, with how much of the change it accounts for."""

    kind: Literal[
        "calendar_days",
        "daily_rate",
        "sales_count",
        "sale_value",
        "volume",
        "price",
        "no_product_detail",
        "contributor",
    ]
    lens: Literal["days", "orders", "price_volume", "parts"]
    label: str  # a short title
    amount: str  # signed, in the figure's own unit
    share_pct: str | None  # of the overall change; negative = it worked against the change
    text: str  # a plain sentence


class LensOut(BaseModel):
    """One way of reading the change: two effects that add up to the whole of it."""

    key: Literal["days", "orders", "price_volume"]
    title: str
    effects: list[FindingOut]


class DriversOut(BaseModel):
    metric: str
    metric_label: str
    unit: Literal["gbp", "percent", "count", "ratio"]
    month: date
    compared_with: date
    against: Literal["previous_month", "last_year"]
    total_previous: str
    total_current: str
    total_change: str
    total_change_pct: str | None
    headline: str
    # Each lens splits the same change in two; they are different readings, not to be added up.
    lenses: list[LensOut]
    # The strongest drivers across every lens and every way of splitting into parts, biggest first.
    findings: list[FindingOut]
