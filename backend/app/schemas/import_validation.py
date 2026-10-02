from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel


class ProblemOut(BaseModel):
    """One kind of problem and how many rows have it."""

    code: str
    field: str | None
    count: int
    rows: list[int]  # the first few row numbers, as the person sees them in their file
    example: str  # a plain-English example, including the cell that caused it


class WarningOut(BaseModel):
    code: str
    message: str


class TotalsOut(BaseModel):
    """What the rows that will be imported add up to (sales and expenses). Pounds, as text."""

    net: str
    vat: str
    gross: str


class ValidationOut(BaseModel):
    validated_at: datetime
    rows: int
    valid: int  # will be imported
    invalid: int  # have a problem; skipped unless the file is fixed and re-uploaded
    duplicate: int  # already in the data or repeated in the file; skipped
    problems: list[ProblemOut]
    totals: TotalsOut | None
    date_from: date | None
    date_to: date | None
    warnings: list[WarningOut]
    can_import: bool


class RowOut(BaseModel):
    row_number: int
    status: Literal["valid", "invalid", "duplicate", "imported", "skipped"]
    errors: list[dict[str, Any]]
    raw: dict[str, str]  # only the columns that were mapped


class RowsOut(BaseModel):
    total: int
    rows: list[RowOut]
