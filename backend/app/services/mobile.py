# ruff: noqa: E501
"""What the mobile apps need from the server (Phase 18): telling an old app to update, the list of
devices a person is signed in on (and signing one out), and the phones that can be sent push
notifications."""

import re
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.integrations.base import utcnow
from app.models.identity import User, UserSession
from app.models.mobile import PushDevice
from app.schemas.mobile import AppConfigOut, PushDeviceOut, SessionOut
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

MAX_DEVICES = 10  # push devices kept per person; the longest unused go first
_VERSION = re.compile(r"^\d{1,4}(\.\d{1,4}){0,2}$")


# --- app versions -------------------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, int, int]:
    """ "1.4" is 1.4.0. Anything but numbers separated by dots is refused."""
    if not _VERSION.match(text or ""):
        raise AppError("That is not an app version number.", code="bad_version", status_code=422)
    major, minor, patch = ([int(p) for p in text.split(".")] + [0, 0])[:3]
    return major, minor, patch


def app_config(platform: str, version: str | None) -> AppConfigOut:
    """Whether the app asking must update (it is below the minimum) or may (a newer one exists)."""
    settings = get_settings()
    minimum, latest = settings.mobile_min_version, settings.mobile_latest_version
    required = version is not None and parse_version(version) < parse_version(minimum)
    available = version is not None and parse_version(version) < parse_version(latest)
    store = settings.ios_store_url if platform == "ios" else settings.android_store_url
    message = None
    if required:
        message = "This version of Vyterlix is no longer supported. Please update to carry on."
    elif available:
        message = "A newer version of Vyterlix is available."
    return AppConfigOut(
        minimum_version=minimum, latest_version=latest, update_required=required,
        update_available=available, store_url=store, message=message,
    )  # fmt: skip


def check_app_version(client: str, version: str | None) -> None:
    """Refuse a sign-in from a phone app that is too old."""
    if client != "mobile" or version is None:
        return
    if parse_version(version) < parse_version(get_settings().mobile_min_version):
        raise AppError(
            "This version of Vyterlix is no longer supported. Please update the app to sign in.",
            code="app_update_required",
            status_code=426,
        )


# --- the devices a person is signed in on ----------------------------------------------------------------


def _live(user_id: uuid.UUID):
    return (
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > func.now(),
    )


def list_sessions(db: Session, user: User, current: uuid.UUID) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession).where(*_live(user.id)).order_by(UserSession.created_at.desc())
    ).all()
    rows.sort(key=lambda s: s.id != current)  # this device first, the rest newest first
    return [
        SessionOut(
            id=s.id, client=s.client, device_name=s.device_name, user_agent=s.user_agent,
            created_at=s.created_at, last_used_at=s.last_used_at, expires_at=s.expires_at,
            current=s.id == current,
        )
        for s in rows
    ]  # fmt: skip


def revoke_session(
    db: Session, user: User, session_id: uuid.UUID, meta: RequestMeta, current: uuid.UUID
) -> None:
    """Sign one of the person's own devices out (a session that is not theirs is simply not found)."""
    session = db.scalar(
        select(UserSession).where(UserSession.id == session_id, *_live(user.id)).with_for_update()
    )
    if session is None:
        raise NotFoundError("That device was not found", code="session_not_found")
    session.revoked_at = func.now()
    _audit_revoked(db, user, session_id, meta, this_device=session_id == current)
    db.commit()


def revoke_other_sessions(db: Session, user: User, current: uuid.UUID, meta: RequestMeta) -> int:
    ended = db.execute(
        update(UserSession).where(*_live(user.id), UserSession.id != current).values(revoked_at=func.now())
    ).rowcount  # fmt: skip
    if ended:
        record_audit(
            db, AuditAction.AUTH_SESSIONS_REVOKED, actor_user_id=user.id, ip_address=meta.ip_address,
            user_agent=meta.user_agent, details={"sessions_ended": ended},
        )  # fmt: skip
    db.commit()
    return ended


def _audit_revoked(
    db: Session, user: User, session_id: uuid.UUID, meta: RequestMeta, *, this_device: bool
) -> None:
    record_audit(
        db, AuditAction.AUTH_SESSION_REVOKED, actor_user_id=user.id, target_type="session",
        target_id=session_id, ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"this_device": this_device},
    )  # fmt: skip


# --- push devices --------------------------------------------------------------------------------------------------


def _out(device: PushDevice) -> PushDeviceOut:
    return PushDeviceOut(
        id=device.id, platform=device.platform, app_version=device.app_version,
        created_at=device.created_at, last_seen_at=device.last_seen_at,
    )  # fmt: skip


def register_device(
    db: Session,
    user: User,
    session_id: uuid.UUID,
    platform: str,
    token: str,
    app_version: str | None,
) -> PushDeviceOut:
    """Remember a phone's push address. The same address always belongs to whoever registered it last
    (a phone handed to someone else must not go on receiving the first person's notifications)."""
    if app_version is not None:
        parse_version(app_version)
    device = db.scalar(select(PushDevice).where(PushDevice.token == token).with_for_update())
    if device is None:
        now = utcnow()
        device = PushDevice(
            created_at=now,
            last_seen_at=now,
            token=token,
            user_id=user.id,
            session_id=session_id,
            platform=platform,
            app_version=app_version,
        )
        db.add(device)
    else:
        device.user_id, device.session_id = user.id, session_id
        device.platform, device.app_version = platform, app_version
        device.last_seen_at = utcnow()
    db.flush()
    keep = db.scalars(
        select(PushDevice.id).where(PushDevice.user_id == user.id).order_by(PushDevice.last_seen_at.desc(), PushDevice.id).limit(MAX_DEVICES)
    ).all()  # fmt: skip
    db.execute(delete(PushDevice).where(PushDevice.user_id == user.id, PushDevice.id.not_in(keep)))
    db.commit()
    db.refresh(device)
    return _out(device)


def list_devices(db: Session, user: User) -> list[PushDeviceOut]:
    """The person's phones that can still be reached (the session they registered from is live)."""
    rows = db.scalars(
        select(PushDevice)
        .join(UserSession, UserSession.id == PushDevice.session_id)
        .where(PushDevice.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > func.now())
        .order_by(PushDevice.last_seen_at.desc(), PushDevice.id)
    ).all()  # fmt: skip
    return [_out(d) for d in rows]


def remove_device(db: Session, user: User, device_id: uuid.UUID) -> None:
    device = db.scalar(
        select(PushDevice).where(PushDevice.id == device_id, PushDevice.user_id == user.id)
    )
    if device is None:
        raise NotFoundError("That phone was not found", code="device_not_found")
    db.delete(device)
    db.commit()


def reachable_tokens(db: Session, user_id: uuid.UUID) -> list[tuple[str, str]]:
    """(platform, token) of every phone a push could be sent to for this person right now."""
    return [
        (platform, token)
        for platform, token in db.execute(
            select(PushDevice.platform, PushDevice.token)
            .join(UserSession, UserSession.id == PushDevice.session_id)
            .where(PushDevice.user_id == user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > func.now())
            .order_by(PushDevice.last_seen_at.desc())
        )
    ]  # fmt: skip
