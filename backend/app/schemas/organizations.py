import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CreateOrganizationRequest(BaseModel):
    # Only the name for now; the business profile (industry, currency, ...) is Phase 3.
    model_config = ConfigDict(extra="forbid")

    name: OrganizationName


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
    status: str
    joined_at: datetime
