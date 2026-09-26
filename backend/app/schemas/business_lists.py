import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.business import LIST_KINDS

ListKind = Literal[LIST_KINDS]  # type: ignore[valid-type]


def _clean_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise ValueError("Enter a name")
    if len(cleaned) > 100:
        raise ValueError("Keep the name to 100 characters or fewer")
    return cleaned


ItemName = Annotated[str, AfterValidator(_clean_name)]


class ListItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ItemName
    is_cost_of_sales: bool | None = None  # cost categories only


class ListItemsBulkCreate(BaseModel):
    """Add several at once, e.g. the suggestions ticked during onboarding."""

    model_config = ConfigDict(extra="forbid")

    names: list[ItemName] = Field(min_length=1, max_length=50)


class ListItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ItemName | None = None
    is_active: bool | None = None  # False = archive; True = restore
    sort_order: int | None = Field(default=None, ge=0, le=1000)
    is_cost_of_sales: bool | None = None

    @model_validator(mode="after")
    def _valid(self) -> "ListItemPatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for required in ("name", "is_active"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} can't be cleared")
        return self


class ListItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    is_active: bool
    sort_order: int | None
    is_cost_of_sales: bool | None
    created_at: datetime


class BulkCreateOut(BaseModel):
    added: list[ListItemOut]
    already_there: list[str]  # names skipped because the list already had them


class BusinessListsOut(BaseModel):
    offering: list[ListItemOut]
    sales_channel: list[ListItemOut]
    customer_type: list[ListItemOut]
    cost_category: list[ListItemOut]
