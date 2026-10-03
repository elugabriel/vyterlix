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
