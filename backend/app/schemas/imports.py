import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SheetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset: str
    source: str
    status: str
    original_filename: str
    file_size_bytes: int
    sheet_name: str | None
    header_row: int
    row_count: int
    valid_count: int
    invalid_count: int
    duplicate_count: int
    imported_count: int
    uploaded_by_user_id: uuid.UUID | None
    created_at: datetime
    imported_at: datetime | None
    undone_at: datetime | None
    file_deleted_at: datetime | None  # originals are removed after 90 days


class PreviewOut(BaseModel):
    """What's in the file: enough for the user to check it and map its columns."""

    headers: list[str]
    sample_rows: list[list[str]]
    row_count: int
    sheets: list[str]  # Excel: every visible sheet
    needs_sheet: bool  # Excel with several sheets: choose one (PATCH sheet_name)
    encoding: str | None
    delimiter: str | None


class EarlierUploadOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    status: str
    created_at: datetime


class ImportUploadedOut(ImportOut):
    preview: PreviewOut
    # The exact same file (byte for byte) was uploaded before: warn, don't block.
    duplicate_of: EarlierUploadOut | None


class ImportDetailOut(ImportOut):
    preview: PreviewOut | None  # None once the original file has been deleted


class ImportPatch(BaseModel):
    """Choose the Excel sheet and/or which row holds the column headings."""

    model_config = ConfigDict(extra="forbid")

    sheet_name: SheetName | None = None
    header_row: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _something_to_change(self) -> "ImportPatch":
        if not self.model_fields_set:
            raise ValueError("Give a sheet_name and/or a header_row")
        return self
