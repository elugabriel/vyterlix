import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.uk import today_uk
from app.models.business import GOAL_STATUSES, GOAL_TYPES, TARGET_UNITS

GoalType = Literal[GOAL_TYPES]  # type: ignore[valid-type]
GoalStatus = Literal[GOAL_STATUSES]  # type: ignore[valid-type]
TargetUnit = Literal[TARGET_UNITS]  # type: ignore[valid-type]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
KpiCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,100}$")]
# NUMERIC(18, 4) in the database; never a float.
Amount = Annotated[Decimal, Field(max_digits=18, decimal_places=4)]


def _check_amount(value: Decimal | None, unit: str | None, label: str) -> None:
    if value is None:
        return
    if unit == "gbp":
        if value < 0:
            raise ValueError(f"{label} in £ can't be negative")
        if value != value.quantize(Decimal("0.01")):
            raise ValueError(f"{label} in £ can have at most 2 decimal places (pence)")
    elif unit == "count":
        if value < 0 or value != value.to_integral_value():
            raise ValueError(f"{label} must be a whole number of 0 or more")
    elif unit == "percent" and not Decimal("-100") <= value <= Decimal("1000"):
        raise ValueError(f"{label} must be between -100% and 1000%")


def _check_target_date(target_date: date | None) -> None:
    if target_date is not None and target_date < today_uk():
        raise ValueError("The target date can't be in the past")


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    goal_type: GoalType
    kpi_code: KpiCode | None = None  # KPIs are defined in Phase 5
    baseline_value: Amount | None = None
    target_value: Amount | None = None
    target_unit: TargetUnit | None = None
    target_date: date | None = None
    priority: int = Field(default=3, ge=1, le=5)
    notes: Notes | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "GoalCreate":
        if (self.target_value is None) != (self.target_unit is None):
            raise ValueError("Give both a target value and its unit (gbp, percent or count)")
        if self.baseline_value is not None and self.target_unit is None:
            raise ValueError("A starting value needs a target and unit to compare against")
        _check_amount(self.target_value, self.target_unit, "The target")
        _check_amount(self.baseline_value, self.target_unit, "The starting value")
        _check_target_date(self.target_date)
        return self


class GoalPatch(BaseModel):
    """Send only what changes. Mark a goal achieved or abandoned via `status`."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    goal_type: GoalType | None = None
    kpi_code: KpiCode | None = None
    baseline_value: Amount | None = None
    target_value: Amount | None = None
    target_unit: TargetUnit | None = None
    target_date: date | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: GoalStatus | None = None
    notes: Notes | None = None

    @model_validator(mode="after")
    def _valid(self) -> "GoalPatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for required in ("title", "goal_type", "priority", "status"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} can't be cleared")
        if "target_date" in self.model_fields_set:
            _check_target_date(self.target_date)
        return self


class GoalOut(BaseModel):
    id: uuid.UUID
    title: str
    goal_type: str
    category: str  # the KPI area this goal belongs to (used for Manager remits)
    kpi_code: str | None
    baseline_value: Decimal | None
    target_value: Decimal | None
    target_unit: str | None
    target_date: date | None
    priority: int
    status: str
    notes: str | None
    created_by: str | None  # creator's name
    created_at: datetime
    updated_at: datetime
