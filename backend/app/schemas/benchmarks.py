from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.uk import today_uk
from app.models.business import BENCHMARK_UNITS, BUSINESS_SIZES, UK_REGIONS


def _blank_to_none(value):
    """Empty CSV cells mean "not specified" (e.g. no region = the whole UK)."""
    if isinstance(value, str) and not value.strip():
        return None
    return value.strip() if isinstance(value, str) else value


BlankIsNone = BeforeValidator(_blank_to_none)
Region = Literal[UK_REGIONS]  # type: ignore[valid-type]
SizeBand = Literal[BUSINESS_SIZES]  # type: ignore[valid-type]
Unit = Literal[BENCHMARK_UNITS]  # type: ignore[valid-type]
Code = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9_]{1,100}$")]
# Constraints go on the text itself; a blank CSV cell becomes None before they apply.
SicCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://", max_length=500)]
NotesText = Annotated[str, StringConstraints(max_length=2000)]


class BenchmarkRow(BaseModel):
    """One row of a benchmark CSV. Every figure must say where it came from."""

    model_config = ConfigDict(extra="forbid")

    industry_code: Code
    sic_code: Annotated[SicCode | None, BlankIsNone] = None
    region: Annotated[Region | None, BlankIsNone] = None  # blank = whole UK
    size_band: Annotated[SizeBand | None, BlankIsNone] = None  # blank = all sizes
    kpi_code: Code
    period_year: int = Field(ge=2000)
    value: Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
    unit: Unit
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    source_url: Annotated[HttpsUrl | None, BlankIsNone] = None
    notes: Annotated[NotesText | None, BlankIsNone] = None

    @model_validator(mode="after")
    def _not_future(self) -> "BenchmarkRow":
        if self.period_year > today_uk().year:
            raise ValueError("period_year can't be in the future")
        return self


class BenchmarkOut(BaseModel):
    industry_code: str
    sic_code: str | None
    region: str | None
    size_band: str | None
    kpi_code: str
    period_year: int
    value: Decimal
    unit: str
    source: str
    source_url: str | None
    notes: str | None


class MatchedBenchmarkOut(BenchmarkOut):
    # How specific the comparison is, e.g. ["sector", "London", "small businesses"].
    matched_on: list[str]
