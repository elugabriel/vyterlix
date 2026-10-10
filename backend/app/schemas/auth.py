import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_serializer,
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
# The one password policy, shared by registration, reset and (step 6) change-password.
# Length-based (NIST SP 800-63B): no composition rules, spaces allowed.
NewPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
EmailedToken = Annotated[str, Field(min_length=20, max_length=200)]


def password_matches_email(password: str, email: str) -> bool:
    return password.strip().lower() == email.lower()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: NewPassword
    full_name: FullName

    @model_validator(mode="after")
    def _password_not_email(self) -> "RegisterRequest":
        if password_matches_email(self.password, self.email):
            raise ValueError("Password must not be the same as your email address")
        return self


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: EmailedToken


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: EmailedToken
    new_password: NewPassword


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    # No minimum here: old accounts may pre-date today's rules. Max stops huge hash inputs.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    # A phone says so, and then gets its refresh token in the answer (to keep in its secure
    # storage) instead of in a browser cookie. It may name itself for the list of devices.
    client: Literal["web", "mobile"] = "web"
    device_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = (
        None
    )
    app_version: Annotated[str, Field(max_length=20)] | None = None


class RefreshRequest(BaseModel):
    """What a phone sends to refresh or to log out (a browser sends its cookie and no body)."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: Annotated[str, Field(min_length=20, max_length=200)]


class UpdateProfileRequest(BaseModel):
    """Only fields the user may change themselves. Email changes need re-verification
    and are handled separately (not yet built)."""

    model_config = ConfigDict(extra="forbid")

    full_name: FullName | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "UpdateProfileRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if "full_name" in self.model_fields_set and self.full_name is None:
            raise ValueError("full_name cannot be empty")
        return self


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: NewPassword


class MessageOut(BaseModel):
    message: str


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


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserOut
    # Only for a phone: a browser's refresh token is in a cookie it can't read.
    refresh_token: str | None = None

    @model_serializer(mode="wrap")
    def _no_empty_refresh_token(self, handler):
        data = handler(self)
        if data.get("refresh_token") is None:
            data.pop("refresh_token", None)  # a browser's answer has no such field at all
        return data
