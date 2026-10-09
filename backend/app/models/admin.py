"""The people who look after the platform itself (Phase 17).

Platform staff are not members of any business. A staff member sees what they need to look after
accounts (businesses, people, plans, the audit trail) and never the figures inside a business.
Staff rights are only ever given from the command line (`python -m app.cli.staff`), never from
the website.
"""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, text
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
