from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.schemas.mobile import AppConfigOut
from app.services import mobile

router = APIRouter(prefix="/app-config", tags=["mobile"])


@router.get("", response_model=AppConfigOut)
def app_config(
    platform: Literal["ios", "android"],
    version: Annotated[str | None, Query(max_length=20)] = None,
) -> AppConfigOut:
    """No login needed: an app asks this when it opens, to learn whether it must (or may) update."""
    return mobile.app_config(platform, version)
