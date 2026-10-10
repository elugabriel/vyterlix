import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import DB, CurrentPrincipal, CurrentUser, Meta
from app.schemas.auth import ChangePasswordRequest, MessageOut, UpdateProfileRequest, UserOut
from app.schemas.mobile import PushDeviceIn, PushDeviceOut, RevokedOut, SessionOut
from app.services import mobile
from app.services.email import EmailSender, get_email_sender
from app.services.profile import change_password, update_profile

router = APIRouter(prefix="/me", tags=["me"])

# Everything here is the user's own account, so it works before email verification.


@router.get("", response_model=UserOut)
def read_me(user: CurrentUser) -> UserOut:
    return UserOut.from_user(user)


@router.patch("", response_model=UserOut)
def update_me(body: UpdateProfileRequest, user: CurrentUser, db: DB, meta: Meta) -> UserOut:
    changes = body.model_dump(include=body.model_fields_set)
    return UserOut.from_user(update_profile(db, user, changes, meta))


@router.post("/change-password", response_model=MessageOut)
def change_my_password(
    body: ChangePasswordRequest,
    principal: CurrentPrincipal,
    db: DB,
    meta: Meta,
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> MessageOut:
    change_password(
        db,
        principal.user,
        principal.session_id,
        body.current_password,
        body.new_password,
        sender,
        meta,
    )
    return MessageOut(message="Your password has been changed. Other devices have been logged out.")


@router.get("/sessions", response_model=list[SessionOut])
def my_sessions(principal: CurrentPrincipal, db: DB) -> list[SessionOut]:
    """The devices signed in to this account, this one first."""
    return mobile.list_sessions(db, principal.user, principal.session_id)


@router.post("/sessions/revoke-others", response_model=RevokedOut)
def sign_out_other_devices(principal: CurrentPrincipal, db: DB, meta: Meta) -> RevokedOut:
    ended = mobile.revoke_other_sessions(db, principal.user, principal.session_id, meta)
    return RevokedOut(sessions_ended=ended)


@router.delete("/sessions/{session_id}", status_code=204)
def sign_out_a_device(session_id: uuid.UUID, principal: CurrentPrincipal, db: DB, meta: Meta):
    mobile.revoke_session(db, principal.user, session_id, meta, principal.session_id)
    return Response(status_code=204)


@router.get("/push-devices", response_model=list[PushDeviceOut])
def my_push_devices(user: CurrentUser, db: DB) -> list[PushDeviceOut]:
    return mobile.list_devices(db, user)


@router.post("/push-devices", response_model=PushDeviceOut)
def register_push_device(body: PushDeviceIn, principal: CurrentPrincipal, db: DB) -> PushDeviceOut:
    """A phone says where to send it notifications. Safe to repeat: an address is kept once."""
    return mobile.register_device(
        db, principal.user, principal.session_id, body.platform, body.token, body.app_version
    )


@router.delete("/push-devices/{device_id}", status_code=204)
def forget_push_device(device_id: uuid.UUID, user: CurrentUser, db: DB):
    mobile.remove_device(db, user, device_id)
    return Response(status_code=204)
