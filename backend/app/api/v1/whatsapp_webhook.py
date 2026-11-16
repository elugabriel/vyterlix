import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import DB
from app.core.errors import AppError
from app.services import whatsapp as service

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

MAX_BODY = 1_000_000  # bytes: a notice is small; anything bigger is not from WhatsApp


@router.get("/{integration_id}", response_class=PlainTextResponse)
def verify(
    integration_id: uuid.UUID,
    db: DB,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge", max_length=200),
):
    """WhatsApp checks, when the address is saved, that it belongs to us. Answered with its own
    challenge word, only if the check word is right."""
    return service.verify(db, integration_id, mode, token, challenge)


@router.post("/{integration_id}")
async def receive(integration_id: uuid.UUID, request: Request, db: DB):
    """Notices of messages. Nothing is believed without a genuine signature."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY:
        raise AppError("That message is too large.", code="too_large", status_code=413)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise AppError("That message is too large.", code="too_large", status_code=413)
    return await run_in_threadpool(
        service.receive, db, integration_id, request.headers.get("x-hub-signature-256"), body
    )
