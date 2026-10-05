"""Request body size limits, enforced before a body is read into memory or onto disk.

Normal JSON requests are small: anything over 1 MB is refused. File uploads
(`POST /api/v1/organizations/{id}/imports`) may be up to the upload limit plus a little
for the multipart wrapping. The limit is checked against the declared Content-Length
first (cheap, immediate) and then against the bytes actually received, because the
declared length can be missing or false.
"""

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_response

JSON_LIMIT_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
_UPLOAD_PATH = re.compile(r"^/api/v1/organizations/[^/]+/imports/?$")


_EVIDENCE_PATH = re.compile(r"^/api/v1/organizations/[^/]+/actions/[^/]+/evidence/file/?$")
EVIDENCE_LIMIT_BYTES = 5 * 1024 * 1024


class BodyTooLargeError(AppError):
    status_code = 413
    code = "request_too_large"


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, upload_limit: int) -> None:
        self.app = app
        self.upload_max = upload_limit
        self.upload_limit = upload_limit + MULTIPART_OVERHEAD_BYTES

    def _limit_for(self, scope: Scope) -> tuple[int, int, str]:
        """(bytes refused above, bytes named in the message, error code)."""
        if scope["method"] == "POST" and _UPLOAD_PATH.match(scope["path"]):
            return self.upload_limit, self.upload_max, "file_too_large"
        if scope["method"] == "POST" and _EVIDENCE_PATH.match(scope["path"]):
            return (
                EVIDENCE_LIMIT_BYTES + MULTIPART_OVERHEAD_BYTES,
                EVIDENCE_LIMIT_BYTES,
                "file_too_large",
            )
        return JSON_LIMIT_BYTES, JSON_LIMIT_BYTES, "request_too_large"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit, named, code = self._limit_for(scope)
        message = f"That is too large: the limit is {named // (1024 * 1024)} MB"

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            response = error_response(413, code, message)
            await response(scope, receive, send)
            return

        received = 0
        exceeded = False
        answered = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            event = await receive()
            if event["type"] == "http.request":
                received += len(event.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise BodyTooLargeError(message, code=code)  # stops the body being read
            return event

        async def limited_send(event: Message) -> None:
            # Frameworks turn a failure while reading a body into their own generic 400.
            # Once the limit has been passed, that answer is replaced by the real reason.
            nonlocal answered
            if not exceeded:
                await send(event)
            elif not answered:
                answered = True
                await error_response(413, code, message)(scope, receive, send)

        await self.app(scope, limited_receive, limited_send)
