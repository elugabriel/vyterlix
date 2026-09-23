import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

from app.models.identity import User

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def _lowercase(value: str) -> str:
    # EmailStr only lowercases the domain; accounts are keyed on the whole address.
    return value.lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(_lowercase)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    # Length-based policy (NIST SP 800-63B): no composition rules, spaces allowed.
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    full_name: FullName

    @model_validator(mode="after")
    def _password_not_email(self) -> "RegisterRequest":
        if self.password.strip().lower() == self.email:
            raise ValueError("Password must not be the same as your email address")
        return self


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    email_verified: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        )
