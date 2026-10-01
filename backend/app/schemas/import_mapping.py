import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
VatRate = Literal["20", "5", "0"]  # UK rates, as text


class MappingOptions(BaseModel):
    """Answers that only the person who knows the file can give."""

    model_config = ConfigDict(extra="forbid")

    # Do the amounts in the file include VAT? Asked for every import (never assumed).
    vat_inclusive: bool | None = None
    # The UK VAT rate to use when the file has no VAT column.
    default_vat_rate: VatRate | None = None


class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # {"field": "Column heading in the file"}; null or "" leaves a field unmapped.
    mapping: dict[str, str | None] = Field(max_length=50)
    options: MappingOptions = MappingOptions()
    # Remember this mapping under a name so the next file from the same place maps itself.
    save_as: SourceName | None = None


class FieldOut(BaseModel):
    key: str
    label: str
    help: str
    type: str
    required: bool
    one_of: list[str] | None  # "at least one of these fields is needed"


class IssueOut(BaseModel):
    code: str
    message: str
    field: str | None


class SavedSourceOut(BaseModel):
    id: uuid.UUID
    name: str


class MappingOut(BaseModel):
    dataset: str
    status: str
    fields: list[FieldOut]
    headers: list[str]  # the file's columns, to choose from
    mapping: dict[str, str]  # what's saved on this import
    options: dict[str, str | bool]
    suggested_mapping: dict[str, str]
    suggested_options: MappingOptions
    suggestion_from: Literal["saved", "automatic"] | None
    saved_source: SavedSourceOut | None  # set when the suggestion came from a saved mapping
    needs_vat_options: bool
    vat_rates: list[str]
    issues: list[IssueOut]  # what's still wrong with the saved mapping
    ready: bool  # saved mapping is complete: validation (next step) can run


class DataSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    dataset: str
    column_mapping: dict[str, str]
    options: dict[str, str | bool]
    last_imported_at: datetime | None
    created_at: datetime
