import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.permissions import KpiCategory

OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CreateOrganizationRequest(BaseModel):
    # Only the name for now; the business profile (industry, currency, ...) is Phase 3.
    model_config = ConfigDict(extra="forbid")

    name: OrganizationName


class Remit(BaseModel):
    """What a Manager may act on. `kpi_categories=None` means every category."""

    model_config = ConfigDict(extra="forbid")

    kpi_categories: list[KpiCategory] | None = Field(default=None, min_length=1)


class UpdateOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OrganizationName


class UpdateMemberRequest(BaseModel):
    """Change a member's role and/or a Manager's remit. Send only what changes."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "manager", "viewer"] | None = None
    remit: Remit | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "UpdateMemberRequest":
        if not self.model_fields_set:
            raise ValueError("Provide role and/or remit")
        if "role" in self.model_fields_set and self.role is None:
            raise ValueError("role cannot be empty")
        return self


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    role: str  # the caller's role in this organisation: owner / manager / viewer
    created_at: datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    remit: Remit | None  # only ever set for managers
    status: str
    joined_at: datetime
