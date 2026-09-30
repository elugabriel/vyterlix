"""The request size guard, tested on its own: an oversized request must be refused
without the application ever being asked to read it."""

import asyncio

from app.core.limits import BodySizeLimitMiddleware

MB = 1024 * 1024
UPLOAD = "/api/v1/organizations/6b1d3c1e-0000-4000-8000-000000000000/imports"


def call(path, *, method="POST", declared=None, body_chunks=()):
    """Run a request through the middleware; returns (status, inner_app_was_called)."""
    called = False
    sent = []

    async def inner(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = [(b"content-length", str(declared).encode())] if declared is not None else []
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    chunks = list(body_chunks)

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(BodySizeLimitMiddleware(inner, upload_limit=25 * MB)(scope, receive, send))
    return sent[0]["status"], called


def test_a_declared_oversize_upload_is_refused_without_running_the_app():
    assert call(UPLOAD, declared=27 * MB) == (413, False)


def test_an_upload_within_the_limit_gets_through():
    assert call(UPLOAD, declared=25 * MB) == (200, True)


def test_a_declared_oversize_json_request_is_refused_without_running_the_app():
    assert call("/api/v1/auth/login", declared=2 * MB) == (413, False)


def test_only_uploads_get_the_large_allowance():
    assert call("/api/v1/organizations/x/imports/abc", declared=5 * MB)[0] == 413
    assert call("/api/v1/organizations/x/seasons", declared=5 * MB)[0] == 413
    assert call(UPLOAD, method="GET", declared=5 * MB)[0] == 413
