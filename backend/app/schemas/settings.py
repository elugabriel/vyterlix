from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.business import NOTIFICATION_CATEGORIES

NotificationCategory = Literal[NOTIFICATION_CATEGORIES]  # type: ignore[valid-type]
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class QuietHours(BaseModel):
    """Non-urgent alerts wait until quiet hours end (UK time). May cross midnight."""

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time

    @field_validator("start", "end")
    @classmethod
    def _whole_minutes(cls, value: time) -> time:
        if value.second or value.microsecond or value.tzinfo:
            raise ValueError("Use a UK time in hours and minutes, e.g. 22:00")
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> "QuietHours":
        if self.start == self.end:
            raise ValueError("Quiet hours must start and end at different times")
        return self


class SettingsPatch(BaseModel):
    """Time zone, locale, date format and currency are fixed to the UK (not editable)."""

    model_config = ConfigDict(extra="forbid")

    week_start_day: int | None = Field(default=None, ge=1, le=7)  # 1 = Monday
    quiet_hours: QuietHours | None = None  # null switches quiet hours off

    @model_validator(mode="after")
    def _something(self) -> "SettingsPatch":
        if not self.model_fields_set:
            raise ValueError("Provide week_start_day and/or quiet_hours")
        if "week_start_day" in self.model_fields_set and self.week_start_day is None:
            raise ValueError("week_start_day can't be cleared")
        return self


class SettingsOut(BaseModel):
    timezone: str
    locale: str
    currency: str
    date_format: str
    week_start_day: int
    week_start_name: str
    quiet_hours: QuietHours | None


class ChannelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: bool | None = None
    in_app: bool | None = None
    push: bool | None = None


class NotificationPreferenceOut(BaseModel):
    category: str
    email: bool
    in_app: bool
    push: bool
    locked: bool  # security: email and in-app can't be switched off


class NotificationPreferencesPatch(BaseModel):
    """{"sales": {"email": false}, "forecast": {"push": false}}: only what changes."""

    model_config = ConfigDict(extra="forbid")

    preferences: dict[NotificationCategory, ChannelChoice] = Field(min_length=1)
