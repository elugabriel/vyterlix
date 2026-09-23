import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.auth import (
    EmailRequest,
    MessageOut,
    RegisterRequest,
    UserOut,
    VerifyEmailRequest,
)
from app.services.auth import (
    RequestMeta,
    register_user,
    resend_verification,
    send_verification_email,
    verify_email,
)
from app.services.email import EmailSender, get_email_sender

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
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> UserOut:
    user = register_user(
        db, email=body.email, password=body.password, full_name=body.full_name, meta=meta
    )
    send_verification_email(db, user, sender, meta)
    return UserOut.from_user(user)


@router.post("/verify-email", response_model=UserOut)
def verify_email_route(
    body: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(request_meta)],
) -> UserOut:
    return UserOut.from_user(verify_email(db, body.token, meta))


@router.post(
    "/resend-verification", status_code=status.HTTP_202_ACCEPTED, response_model=MessageOut
)
def resend_verification_route(
    body: EmailRequest,
    db: Annotated[Session, Depends(get_db)],
    meta: Annotated[RequestMeta, Depends(request_meta)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> MessageOut:
    resend_verification(db, body.email, sender, meta)
    return MessageOut(message="If that account still needs verifying, we've sent a new link to it.")
