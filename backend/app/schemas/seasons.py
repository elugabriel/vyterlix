import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.uk import is_valid_day_of_month

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
# -20 = typically 20% quieter than normal; +40 = 40% busier.
ChangePct = Annotated[Decimal, Field(ge=-100, le=1000, max_digits=6, decimal_places=2)]


class DayOfYear(BaseModel):
    """A day that recurs every year, e.g. 1 December."""

    model_config = ConfigDict(extra="forbid")

    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)

    @model_validator(mode="after")
    def _exists_every_year(self) -> "DayOfYear":
        if not is_valid_day_of_month(self.month, self.day):
            raise ValueError(
                "That date doesn't exist every year (for example 31 April or 29 February)"
            )
        return self


class SeasonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    start: DayOfYear
    end: DayOfYear  # may be earlier in the year than start: the season crosses New Year
    expected_change_pct: ChangePct | None = None
    notes: Notes | None = None


class SeasonPatch(BaseModel):
    """Send only what changes. `status`: "active" confirms/restores, "dismissed" turns off."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    start: DayOfYear | None = None
    end: DayOfYear | None = None
    expected_change_pct: ChangePct | None = None
    notes: Notes | None = None
    status: Literal["active", "dismissed"] | None = None

    @model_validator(mode="after")
    def _valid(self) -> "SeasonPatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for required in ("name", "start", "end", "status"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} can't be cleared")
        return self


class SeasonOut(BaseModel):
    id: uuid.UUID
    name: str
    start: DayOfYear
    end: DayOfYear
    label: str  # UK style, e.g. "1 Dec – 5 Jan"
    crosses_new_year: bool
    expected_change_pct: Decimal | None
    direction: Literal["busier", "quieter", "normal"] | None
    source: str  # "user" or "detected"
    status: str  # "active", "suggested" (detected, awaiting confirmation) or "dismissed"
    notes: str | None
    created_at: datetime
    updated_at: datetime
