"""Alerts and notifications (Phase 14).

- alert_rules: the business's own settings for each kind of alert (on or off, how serious, the
  threshold). A kind that has no row uses its defaults.
- alerts: one thing that needs attention, in one of nine areas, with a severity. The same thing
  happening again is counted on the open alert rather than raised again (see dedupe_key).
- alert_events: everything that happened to an alert, in order (raised, seen again, acknowledged,
  resolved), so its history can always be read back.
- notifications: what each person was told, and whether they have read it. The notification service
  alone decides who is told, how, and when (their preferences, quiet hours, and their role).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.business import NOTIFICATION_CATEGORIES
from app.models.identity import _one_of

SEVERITIES = ("info", "low", "medium", "high", "critical")
ALERT_STATUSES = ("open", "acknowledged", "resolved")
EVENT_KINDS = ("raised", "repeated", "acknowledged", "resolved", "reopened")
EMAIL_STATUSES = ("none", "pending", "sent", "failed")


class AlertRule(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_alert_rules_code"),
        CheckConstraint(_one_of("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_one_of("category", NOTIFICATION_CATEGORIES), name="category_valid"),
        CheckConstraint("category <> 'security' OR enabled", name="security_always_on"),
    )

    code: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Alert(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_alerts_org_id"),
        CheckConstraint(_one_of("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_one_of("category", NOTIFICATION_CATEGORIES), name="category_valid"),
        CheckConstraint(_one_of("status", ALERT_STATUSES), name="status_valid"),
        CheckConstraint("occurrences >= 1", name="occurrences_positive"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        # The same thing is never open twice: a repeat is counted on the alert that is already open.
        Index(
            "uq_alerts_open_dedupe",
            "organization_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
        Index("ix_alerts_org_status", "organization_id", "status", "last_seen_at"),
    )

    rule_code: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(20))
    kpi_category: Mapped[str | None] = mapped_column(String(15))  # the area, for a Manager's remit
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(200))  # the screen that explains it
    dedupe_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(15), server_default="open")
    occurrences: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "alert_id"],
            ["alerts.organization_id", "alerts.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("kind", EVENT_KINDS), name="kind_valid"),
        Index("ix_alert_events_alert", "alert_id", "created_at"),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(15))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One message to one person. It is in their in-app inbox if they have in-app alerts on for its
    area; the email goes out straight away, or after quiet hours, or not at all."""

    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "alert_id"],
            ["alerts.organization_id", "alerts.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_one_of("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_one_of("category", NOTIFICATION_CATEGORIES), name="category_valid"),
        CheckConstraint(_one_of("email_status", EMAIL_STATUSES), name="email_status_valid"),
        Index("ix_notifications_user", "organization_id", "user_id", "created_at"),
        Index("ix_notifications_email_due", "email_status", "email_after"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    alert_id: Mapped[uuid.UUID | None] = mapped_column()  # none for a message that is not an alert
    category: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(200))
    in_app: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email_status: Mapped[str] = mapped_column(String(10), server_default="none")
    email_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
