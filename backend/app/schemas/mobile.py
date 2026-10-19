import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AppConfigOut(BaseModel):
    minimum_version: str
    latest_version: str
    update_required: bool
    update_available: bool
    store_url: str | None
    message: str | None


class SessionOut(BaseModel):
    id: uuid.UUID
    client: Literal["web", "mobile"]
    device_name: str | None
    user_agent: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    current: bool  # the device making this request


class SessionRenameIn(BaseModel):
    """Name a device so it is easy to spot in the list ("Jo's iPhone")."""

    model_config = ConfigDict(extra="forbid")

    device_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class RevokedOut(BaseModel):
    sessions_ended: int


class PushDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["ios", "android"]
    token: Annotated[str, Field(min_length=20, max_length=4096)]
    app_version: Annotated[str, Field(max_length=20)] | None = None


class PushDeviceOut(BaseModel):
    id: uuid.UUID
    platform: str
    app_version: str | None
    created_at: datetime
    last_seen_at: datetime
