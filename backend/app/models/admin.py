"""The people who look after the platform itself (Phase 17).

Platform staff are not members of any business. A staff member sees what they need to look after
accounts (businesses, people, plans, the audit trail) and never the figures inside a business.
Staff rights are only ever given from the command line (`python -m app.cli.staff`), never from
the website.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

STAFF_ROLES = ("support", "admin")  # support: look only. admin: look and change.


class PlatformStaff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_staff"
    __table_args__ = (CheckConstraint(_one_of("role", STAFF_ROLES), name="role_valid"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    role: Mapped[str] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


FLAG_KEY = r"^[a-z][a-z0-9_]{2,40}$"
CASE_STATUSES = ("open", "waiting", "resolved")
CASE_PRIORITIES = ("low", "normal", "high")
EVENT_SEVERITIES = ("info", "warning", "error")


class FeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A switch for a feature that can be turned on for everyone, or for chosen businesses only.

    `enabled` is the answer for every business that has no entry in `org_overrides`
    ({business id: true/false}).
    """

    __tablename__ = "feature_flags"
    __table_args__ = (
        CheckConstraint(f"key ~ '{FLAG_KEY}'", name="key_format"),
        CheckConstraint("length(trim(description)) > 0", name="description_not_blank"),
    )

    key: Mapped[str] = mapped_column(String(41), unique=True)
    description: Mapped[str] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    org_overrides: Mapped[dict[str, bool]] = mapped_column(JSONB, server_default=text("'{}'"))


class SupportCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_cases"
    __table_args__ = (
        CheckConstraint(_one_of("status", CASE_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("priority", CASE_PRIORITIES), name="priority_valid"),
        CheckConstraint("length(trim(subject)) > 0", name="subject_not_blank"),
        CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolved_has_a_time"
        ),
        Index("ix_support_cases_status", "status", "created_at"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    requester_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), server_default="open")
    priority: Mapped[str] = mapped_column(String(10), server_default="normal")
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminNote(UUIDPrimaryKeyMixin, Base):
    """A note staff keep about a case, a business or a person. Never shown to the business."""

    __tablename__ = "admin_notes"
    __table_args__ = (
        CheckConstraint(
            "case_id IS NOT NULL OR organization_id IS NOT NULL OR user_id IS NOT NULL",
            name="has_a_subject",
        ),
        CheckConstraint("length(trim(body)) > 0", name="body_not_blank"),
        Index("ix_admin_notes_case", "case_id", "created_at"),
        Index("ix_admin_notes_org", "organization_id", "created_at"),
        Index("ix_admin_notes_user", "user_id", "created_at"),
    )

    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_cases.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class SystemEvent(UUIDPrimaryKeyMixin, Base):
    """Something that went wrong (or worth knowing) in the running system, kept for staff to see.
    Plain-English messages we wrote ourselves: never a stack trace, a secret or business data."""

    __tablename__ = "system_events"
    __table_args__ = (
        CheckConstraint(_one_of("severity", EVENT_SEVERITIES), name="severity_valid"),
        Index("ix_system_events_created", "created_at"),
        Index("ix_system_events_open", "severity", "created_at"),
    )

    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
