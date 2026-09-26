"""Business settings and each member's notification preferences.

Session must be scoped to the organisation. Phase 14's notification engine reads
`effective_preferences()` and `quiet_hours()`; security alerts ignore both.
"""

import uuid
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.uk import CURRENCY
from app.models.business import NOTIFICATION_CATEGORIES, BusinessSettings, NotificationPreference
from app.schemas.settings import (
    WEEKDAYS,
    NotificationPreferenceOut,
    NotificationPreferencesPatch,
    QuietHours,
    SettingsOut,
    SettingsPatch,
)
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

UK_DEFAULTS = {"timezone": "Europe/London", "locale": "en-GB", "week_start_day": 1}
DEFAULT_CHANNELS = {"email": True, "in_app": True, "push": True}
LOCKED_CATEGORY = "security"


# --- business settings ---------------------------------------------------------------------


def _settings_row(db: Session) -> BusinessSettings | None:
    return db.scalar(select(BusinessSettings))  # tenant-scoped: this business's row


def _settings_out(row: BusinessSettings | None) -> SettingsOut:
    week_start = row.week_start_day if row else UK_DEFAULTS["week_start_day"]
    quiet = (
        QuietHours(start=row.quiet_hours_start, end=row.quiet_hours_end)
        if row and row.quiet_hours_start is not None
        else None
    )
    return SettingsOut(
        timezone=UK_DEFAULTS["timezone"],
        locale=UK_DEFAULTS["locale"],
        currency=CURRENCY,
        date_format="dd/mm/yyyy",
        week_start_day=week_start,
        week_start_name=WEEKDAYS[week_start - 1],
        quiet_hours=quiet,
    )


def get_business_settings(db: Session) -> SettingsOut:
    """The saved settings, or the UK defaults if the business hasn't changed any."""
    return _settings_out(_settings_row(db))


def quiet_hours(db: Session) -> tuple[time, time] | None:
    row = _settings_row(db)
    return (row.quiet_hours_start, row.quiet_hours_end) if row and row.quiet_hours_start else None


def update_settings(db: Session, tenant, body: SettingsPatch, meta: RequestMeta) -> SettingsOut:
    row = _settings_row(db)
    if row is None:
        row = BusinessSettings()
        db.add(row)
        db.flush()
        db.refresh(row)

    changes = {}
    if "week_start_day" in body.model_fields_set:
        changes["week_start_day"] = body.week_start_day
    if "quiet_hours" in body.model_fields_set:
        changes["quiet_hours_start"] = body.quiet_hours.start if body.quiet_hours else None
        changes["quiet_hours_end"] = body.quiet_hours.end if body.quiet_hours else None

    changed = sorted(k for k, v in changes.items() if getattr(row, k) != v)
    for name in changed:
        setattr(row, name, changes[name])
    if changed:
        record_audit(
            db,
            AuditAction.SETTINGS_UPDATED,
            actor_user_id=tenant.user.id,
            organization_id=tenant.organization_id,
            target_type="business_settings",
            target_id=row.id,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            details={"fields": changed},
        )
    db.commit()
    db.refresh(row)
    return _settings_out(row)


# --- notification preferences ----------------------------------------------------------------


def effective_preferences(db: Session, user_id: uuid.UUID) -> list[NotificationPreferenceOut]:
    """Every category for this member: saved choices, else defaults (everything on)."""
    saved = {
        p.category: p
        for p in db.scalars(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        )
    }
    result = []
    for category in NOTIFICATION_CATEGORIES:
        row = saved.get(category)
        channels = (
            {"email": row.email, "in_app": row.in_app, "push": row.push}
            if row
            else DEFAULT_CHANNELS
        )
        result.append(
            NotificationPreferenceOut(
                category=category, locked=category == LOCKED_CATEGORY, **channels
            )
        )
    return result


def update_preferences(
    db: Session, user_id: uuid.UUID, body: NotificationPreferencesPatch
) -> list[NotificationPreferenceOut]:
    for category, choice in body.preferences.items():
        sent = choice.model_dump(exclude_none=True)
        if category == LOCKED_CATEGORY and (
            sent.get("email") is False or sent.get("in_app") is False
        ):
            raise AppError(
                "Security alerts are always sent by email and in the app, to keep your "
                "account safe. You can only change push notifications for them.",
                code="security_alerts_required",
                status_code=422,
            )
        if not sent:
            continue
        row = db.scalar(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.category == category,
            )
        )
        if row is None:
            row = NotificationPreference(user_id=user_id, category=category, **DEFAULT_CHANNELS)
            db.add(row)
        for channel, value in sent.items():
            setattr(row, channel, value)
    db.commit()
    return effective_preferences(db, user_id)
