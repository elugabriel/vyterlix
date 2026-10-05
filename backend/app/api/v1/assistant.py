import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.ai.provider import Provider
from app.api.deps import DB, Meta, Tenant, require_permission
from app.core.permissions import Perm
from app.schemas.assistant import (
    AiSettingsIn,
    AiSettingsOut,
    AnswerOut,
    AskIn,
    ConversationDetailOut,
    ConversationOut,
)
from app.services import assistant as service

router = APIRouter(prefix="/organizations/{organization_id}/assistant", tags=["assistant"])

Viewer = Annotated[Tenant, Depends(require_permission(Perm.INSIGHTS_VIEW))]
Owner = Annotated[Tenant, Depends(require_permission(Perm.ORG_MANAGE))]


def get_ai_provider() -> Provider | None:
    """The outside provider set up for this installation (tests replace this)."""
    return service.configured_provider()


AiProvider = Annotated[Provider | None, Depends(get_ai_provider)]


@router.post("/ask", response_model=AnswerOut)
def ask(body: AskIn, tenant: Viewer, db: DB, meta: Meta, provider: AiProvider):
    """Ask a question about the business. The answer is made only from its own results, and says
    where each part came from. A question that is not understood is answered with an honest no."""
    return service.ask(db, tenant, body, meta, provider)


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(tenant: Viewer, db: DB):
    """Your own conversations, newest first. Nobody else in the business can read them."""
    return service.list_conversations(db, tenant)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def conversation(conversation_id: uuid.UUID, tenant: Viewer, db: DB):
    return service.get_conversation(db, tenant, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(conversation_id: uuid.UUID, tenant: Viewer, db: DB):
    service.delete_conversation(db, tenant, conversation_id)


@router.get("/settings", response_model=AiSettingsOut)
def settings(tenant: Viewer, db: DB, provider: AiProvider):
    return service.settings_out(db, provider)


@router.put("/settings", response_model=AiSettingsOut)
def set_settings(body: AiSettingsIn, tenant: Owner, db: DB, meta: Meta, provider: AiProvider):
    """The owner's controls: switch the assistant off, or allow outside AI to reword answers."""
    return service.set_settings(db, tenant, body, provider, meta)
