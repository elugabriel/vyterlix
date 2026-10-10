"""Phones and tablets signed in to Vyterlix (Phase 18)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

PLATFORMS = ("ios", "android")


class PushDevice(UUIDPrimaryKeyMixin, Base):
    """A phone that can be sent push notifications. It belongs to the person and to the session
    (sign-in) it registered from, so it stops being used the moment that session ends."""

    __tablename__ = "push_devices"
    __table_args__ = (
        CheckConstraint(_one_of("platform", PLATFORMS), name="platform_valid"),
        CheckConstraint("length(token) >= 20", name="token_long_enough"),
        Index("ix_push_devices_user", "user_id", "last_seen_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE")
    )
    platform: Mapped[str] = mapped_column(String(10))
    token: Mapped[str] = mapped_column(String(4096), unique=True)
    app_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
