from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.onboarding import OPTIONAL_KEYS

OptionalSection = Literal[OPTIONAL_KEYS]  # type: ignore[valid-type]


class SectionOut(BaseModel):
    key: str
    title: str
    required: bool
    status: Literal["done", "to_do", "skipped"]
    hint: str


class OnboardingOut(BaseModel):
    ready_for_dashboard: bool  # every required section done
    completed_at: datetime | None  # when the owner finished the setup wizard
    done: int
    total: int
    required_done: int
    required_total: int
    optional_done: int
    optional_skipped: int
    optional_total: int
    next_section: str | None  # what to show next; None when nothing is left to do
    sections: list[SectionOut]


class SkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: OptionalSection
    skip: bool = True  # false = bring a skipped section back
