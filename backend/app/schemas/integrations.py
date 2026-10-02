import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderOut(BaseModel):
    key: str
    label: str
    # What we will read, in plain English. Shown before the person connects.
    permissions: list[str]
    read_only: bool = True


class ConnectIn(BaseModel):
    provider: str = Field(pattern=r"^[a-z][a-z_]*$", max_length=30)
    # Re-connecting an existing connection (sign in again): it must be the same account.
    integration_id: uuid.UUID | None = None


class ConnectOut(BaseModel):
    authorize_url: str
    expires_in_minutes: int


class CallbackIn(BaseModel):
    state: str = Field(min_length=10, max_length=200)
    code: str = Field(min_length=1, max_length=2000)


class IntegrationOut(BaseModel):
    """A connection as shown to people. Deliberately has no field for tokens or keys."""

    id: uuid.UUID
    provider: str
    provider_label: str
    display_name: str
    status: Literal["connected", "needs_reauth", "disconnected"]
    account_name: str | None
    permissions: list[str]
    connected_at: datetime
    disconnected_at: datetime | None
    last_sync_at: datetime | None
    last_successful_sync_at: datetime | None
    last_sync_status: Literal["succeeded", "failed"] | None
    last_error_code: str | None
    last_error_message: str | None
    last_error_at: datetime | None
    consecutive_failures: int
    needs_attention: bool


class SyncOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["running", "succeeded", "failed"]
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    records_fetched: int
    records_created: int
    error_code: str | None
    error_message: str | None
    data_import_id: uuid.UUID | None
