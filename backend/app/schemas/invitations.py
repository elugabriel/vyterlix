import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.auth import EmailedToken, NormalizedEmail
from app.schemas.organizations import OrganizationOut, Remit

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    role: Literal["owner", "manager", "viewer"]
    remit: Remit | None = None

    @model_validator(mode="after")
    def _remit_only_for_managers(self) -> "CreateInvitationRequest":
        if self.remit is not None and self.role != "manager":
            raise ValueError("A remit can only be set for managers")
        return self


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    remit: Remit | None
    status: InvitationStatus
    invited_by: str | None  # inviter's name
    created_at: datetime
    expires_at: datetime


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: EmailedToken


class InvitationPreviewOut(BaseModel):
    """What the accept page shows before the invitee logs in or registers."""

    organization_name: str
    email: str
    role: str
    invited_by: str | None
    expires_at: datetime


class AcceptInvitationOut(BaseModel):
    organization: OrganizationOut
