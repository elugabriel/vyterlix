from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import DB, Meta
from app.core.config import get_settings
from app.core.errors import AuthenticationError, error_response
from app.schemas.auth import (
    EmailRequest,
    LoginRequest,
    MessageOut,
    RegisterRequest,
    TokenOut,
    UserOut,
    VerifyEmailRequest,
)
from app.services import sessions
from app.services.auth import (
    register_user,
    resend_verification,
    send_verification_email,
    verify_email,
)
from app.services.email import EmailSender, get_email_sender

router = APIRouter(prefix="/auth", tags=["auth"])

Sender = Annotated[EmailSender, Depends(get_email_sender)]

REFRESH_COOKIE = "vyterlix_refresh"
# The browser only sends the refresh cookie to /auth endpoints, never to data endpoints.
REFRESH_COOKIE_PATH = "/api/v1/auth"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # page JavaScript can never read it
        secure=settings.cookie_secure,
        samesite="strict",  # never sent on requests started by another site
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _token_out(tokens: sessions.IssuedTokens) -> TokenOut:
    return TokenOut(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserOut.from_user(tokens.user),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def register(body: RegisterRequest, db: DB, meta: Meta, sender: Sender) -> UserOut:
    user = register_user(
        db, email=body.email, password=body.password, full_name=body.full_name, meta=meta
    )
    send_verification_email(db, user, sender, meta)
    return UserOut.from_user(user)


@router.post("/verify-email", response_model=UserOut)
def verify_email_route(body: VerifyEmailRequest, db: DB, meta: Meta) -> UserOut:
    return UserOut.from_user(verify_email(db, body.token, meta))


@router.post(
    "/resend-verification", status_code=status.HTTP_202_ACCEPTED, response_model=MessageOut
)
def resend_verification_route(body: EmailRequest, db: DB, meta: Meta, sender: Sender) -> MessageOut:
    resend_verification(db, body.email, sender, meta)
    return MessageOut(message="If that account still needs verifying, we've sent a new link to it.")


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, response: Response, db: DB, meta: Meta) -> TokenOut:
    tokens = sessions.login(db, body.email, body.password, meta)
    _set_refresh_cookie(response, tokens.refresh_token)
    return _token_out(tokens)


@router.post("/refresh", response_model=TokenOut)
def refresh(response: Response, db: DB, refresh_token: RefreshCookie = None):
    try:
        tokens = sessions.refresh(db, refresh_token)
    except AuthenticationError as exc:
        # A dead refresh cookie is useless; tell the browser to drop it.
        failed: JSONResponse = error_response(exc.status_code, exc.code, exc.message)
        _clear_refresh_cookie(failed)
        return failed
    _set_refresh_cookie(response, tokens.refresh_token)
    return _token_out(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DB, meta: Meta, refresh_token: RefreshCookie = None) -> None:
    sessions.logout(db, refresh_token, meta)
    _clear_refresh_cookie(response)
