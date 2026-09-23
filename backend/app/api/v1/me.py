from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.auth import UserOut

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserOut)
def read_me(user: CurrentUser) -> UserOut:
    """The logged-in user's own account. Works before email verification (limited access)."""
    return UserOut.from_user(user)
