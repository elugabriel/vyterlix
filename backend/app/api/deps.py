"""Shared FastAPI dependencies: who is calling, and are they allowed in."""

import ipaddress
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import AccessTokenError, decode_access_token
from app.db.session import get_db
from app.models.identity import User
from app.services.auth import RequestMeta
from app.services.sessions import load_session_user

DB = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def _client_ip(request: Request) -> str | None:
    # TODO(deployment): read the client IP from a trusted proxy header once behind a load balancer.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:  # e.g. unix socket or test client — not an IP, don't store it
        return None


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=_client_ip(request), user_agent=request.headers.get("user-agent"))


Meta = Annotated[RequestMeta, Depends(request_meta)]

_TOKEN_MESSAGES = {
    "token_expired": "Your access token has expired. Refresh it and try again.",
    "invalid_token": "Your access token is not valid. Please log in again.",
}


@dataclass(frozen=True)
class Principal:
    """The caller: which user, on which session (device)."""

    user: User
    session_id: uuid.UUID


def get_principal(
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Any logged-in user, verified or not (limited access)."""
    if credentials is None:
        raise AuthenticationError("Please log in to continue")
    try:
        user_id, session_id = decode_access_token(credentials.credentials)
    except AccessTokenError as exc:
        raise AuthenticationError(_TOKEN_MESSAGES[exc.code], code=exc.code) from exc

    user = load_session_user(db, user_id, session_id)
    if user is None:
        raise AuthenticationError(
            "Your session has ended. Please log in again.", code="session_ended"
        )
    return Principal(user=user, session_id=session_id)


def get_current_user(principal: Annotated[Principal, Depends(get_principal)]) -> User:
    return principal.user


def require_verified_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Logged in AND email verified. Use this for everything beyond the user's own account."""
    if user.email_verified_at is None:
        raise PermissionDeniedError(
            "Please verify your email address to continue", code="email_not_verified"
        )
    return user


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(require_verified_user)]
