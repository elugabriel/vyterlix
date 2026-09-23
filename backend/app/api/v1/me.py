from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentPrincipal, CurrentUser, Meta
from app.schemas.auth import ChangePasswordRequest, MessageOut, UpdateProfileRequest, UserOut
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
