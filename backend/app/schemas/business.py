import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.uk import is_valid_day_of_month, normalise_postcode, normalise_vat_number, today_uk
from app.models.business import BUSINESS_MODELS, BUSINESS_SIZES, UK_REGIONS

Region = Literal[UK_REGIONS]  # type: ignore[valid-type]
BusinessSize = Literal[BUSINESS_SIZES]  # type: ignore[valid-type]
BusinessModel = Literal[BUSINESS_MODELS]  # type: ignore[valid-type]

Postcode = Annotated[str, AfterValidator(normalise_postcode)]
VatNumber = Annotated[str, AfterValidator(normalise_vat_number)]
SicCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}$")]
TownCity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class IndustryOut(BaseModel):
    code: str
    label: str


class FinancialYearStart(BaseModel):
    """Day and month the business's financial year starts, e.g. 1 April."""

    model_config = ConfigDict(extra="forbid")

    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)

    @model_validator(mode="after")
    def _exists_every_year(self) -> "FinancialYearStart":
        if not is_valid_day_of_month(self.month, self.day):
            raise ValueError(
                "That date doesn't exist every year (for example 31 April or 29 February)"
            )
        return self


class _ProfileFields(BaseModel):
    """Optional fields: can be completed later (decision 2026-09-26)."""

    model_config = ConfigDict(extra="forbid")

    sic_code: SicCode | None = None
    region: Region | None = None
    town_city: TownCity | None = None
    postcode: Postcode | None = None
    business_size: BusinessSize | None = None
    business_model: BusinessModel | None = None
    team_size: int | None = Field(default=None, ge=0, le=1_000_000)
    founded_year: int | None = Field(default=None, ge=1800)
    vat_registered: bool | None = None
    vat_number: VatNumber | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.founded_year is not None and self.founded_year > today_uk().year:
            raise ValueError("The year founded can't be in the future")
        if self.vat_number is not None and self.vat_registered is False:
            raise ValueError("A VAT number was given but the business isn't VAT registered")
        return self


class BusinessProfileIn(_ProfileFields):
    """Create or replace the whole profile (PUT). Omitted optional fields are cleared."""

    industry_code: str = Field(min_length=1, max_length=50)
    financial_year_start: FinancialYearStart
    # UK-first: only GBP / GB at launch. Accepted so clients can be explicit.
    currency: Literal["GBP"] = "GBP"
    country: Literal["GB"] = "GB"


class BusinessProfilePatch(_ProfileFields):
    """Change only the fields sent (PATCH). Required fields can be changed but not cleared."""

    industry_code: str | None = Field(default=None, min_length=1, max_length=50)
    financial_year_start: FinancialYearStart | None = None

    @model_validator(mode="after")
    def _something_valid_to_change(self) -> "BusinessProfilePatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for required in ("industry_code", "financial_year_start"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} can't be cleared")
        return self


class BusinessProfileOut(BaseModel):
    organization_id: uuid.UUID
    name: str  # the organisation's name; change it with PATCH /organizations/{id}
    industry: IndustryOut
    financial_year_start: FinancialYearStart
    currency: str
    country: str
    sic_code: str | None
    region: str | None
    town_city: str | None
    postcode: str | None
    business_size: str | None
    business_model: str | None
    team_size: int | None
    founded_year: int | None
    years_operating: int | None  # worked out from founded_year, so it never goes stale
    vat_registered: bool | None
    vat_number: str | None
    onboarding_completed_at: datetime | None
    updated_at: datetime
