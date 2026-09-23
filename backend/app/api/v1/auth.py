import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.auth import RegisterRequest, UserOut
from app.services.auth import RequestMeta, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    # TODO(deployment): read the client IP from a trusted proxy header once behind a load balancer.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:  # e.g. unix socket or test client — not an IP, don't store it
        return None


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=_client_ip(request), user_agent=request.headers.get("user-agent"))


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def register(
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(request_meta)],
) -> UserOut:
    user = register_user(
        db, email=body.email, password=body.password, full_name=body.full_name, meta=meta
    )
    return UserOut.from_user(user)
