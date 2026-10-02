import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Band = Literal["good", "fair", "poor"]


class ComponentOut(BaseModel):
    """One check inside a dataset's score, with the numbers behind it."""

    key: str
    label: str
    score: int  # 0-100
    weight: float  # share of the dataset's score
    detail: str  # plain English, with the actual figures


class DatasetScoreOut(BaseModel):
    dataset: str
    label: str
    records: int
    score: int | None  # None when there is nothing to judge yet
    band: Band | None
    weight: int  # share of the overall score (sales count most)
    components: list[ComponentOut]


class MonthOut(BaseModel):
    month: date  # the first day of the month
    label: str  # "March 2026"
    sales_records: int
    expenses_records: int
    cost_coverage_pct: int | None  # share of that month's sales value with a cost recorded
    categorised_pct: int | None  # share of that month's expense value with a category
    score: int | None


class ImportQualityOut(BaseModel):
    import_id: uuid.UUID
    filename: str
    dataset: str
    imported_at: datetime | None
    rows_imported: int
    rows_with_problems: int
    clean_rate_pct: int


class OriginOut(BaseModel):
    source: str  # manual, csv, excel (later: xero, shopify ...)
    dataset: str
    records: int


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dataset: str
    issue_type: str
    severity: Literal["info", "warning", "critical"]
    period_start: date | None
    period_end: date | None
    affected_count: int
    message: str
    fix: str  # what to do about it
    details: dict[str, Any]
    import_id: uuid.UUID | None = None


class DataQualityOut(BaseModel):
    as_of: date
    score: int | None  # None until there are sales to judge
    band: Band | None
    headline: str
    datasets: list[DatasetScoreOut]
    missing_datasets: list[str]  # needed for a full picture but with no records yet
    months: list[MonthOut]  # newest first, up to 24
    imports: list[ImportQualityOut]  # the most recent imports, newest first
    origins: list[OriginOut]  # where the records came from
    issues: list[IssueOut]  # most serious first


class RefreshOut(DataQualityOut):
    new_issues: int
    resolved_issues: int


class StoredIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset: str
    issue_type: str
    severity: str
    period_start: date | None
    period_end: date | None
    affected_count: int
    message: str
    details: dict[str, Any]
    import_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None
