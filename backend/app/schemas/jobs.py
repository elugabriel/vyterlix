import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, computed_field


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    subject_type: str
    subject_id: uuid.UUID
    progress_done: int
    progress_total: int
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def percent(self) -> int | None:
        """0-100 while running, None when the total isn't known yet."""
        if self.status == "succeeded":
            return 100
        if self.progress_total <= 0:
            return None
        return min(100, int(100 * self.progress_done / self.progress_total))


class ImportJobIn(BaseModel):
    action: Literal["validate", "import", "undo"]
