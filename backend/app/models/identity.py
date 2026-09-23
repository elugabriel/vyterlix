"""Identity, tenancy and access control (Phase 2).

Organization = the customer's business. Users join organizations through
OrganizationUser, which carries their role. Secrets (session refresh tokens,
password-reset and email-verification tokens) are stored as SHA-256 hashes only;
the raw token exists only in the email/cookie sent to the user.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

SYSTEM_ROLES = ("owner", "manager", "viewer")
TOKEN_PURPOSES = ("password_reset", "email_verification")
MEMBERSHIP_STATUSES = ("active", "suspended")
ORGANIZATION_STATUSES = ("active", "suspended", "closed")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["OrganizationUser"]] = relationship(
        back_populates="user", foreign_keys="OrganizationUser.user_id"
    )


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(_one_of("status", ORGANIZATION_STATUSES), name="status_valid"),
    )

    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Decided 2026-09-21: model-improvement use of data is per-organization opt-in, default OFF.
    data_improvement_opt_in: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    members: Mapped[list["OrganizationUser"]] = relationship(back_populates="organization")


role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(300))


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """System roles (owner/manager/viewer) have organization_id NULL.

    Custom per-organization roles (Phase 2+ Corporate feature) set organization_id.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "code"),
        Index(
            "uq_roles_system_code",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions)


class OrganizationUser(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Membership: which user belongs to which organization, with which role."""

    __tablename__ = "organization_users"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        CheckConstraint(_one_of("status", MEMBERSHIP_STATUSES), name="status_valid"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    # Manager "remit" (KPI categories / business units they may act on). Defined in step 9.
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    organization: Mapped[Organization] = relationship(back_populates="members")
    role: Mapped[Role] = relationship()


class UserSession(UUIDPrimaryKeyMixin, Base):
    """One row per logged-in device. Refresh token stored hashed."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserToken(UUIDPrimaryKeyMixin, Base):
    """Single-use, expiring tokens for forgot-password and email verification."""

    __tablename__ = "user_tokens"
    __table_args__ = (
        CheckConstraint(_one_of("purpose", TOKEN_PURPOSES), name="purpose_valid"),
        Index("ix_user_tokens_user_purpose", "user_id", "purpose"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(INET)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only. organization_id is NULL for account-level events (login, password reset)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_created", "organization_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
