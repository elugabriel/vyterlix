import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Question
    conversation_id: uuid.UUID | None = None  # leave out to start a new conversation


class SourceOut(BaseModel):
    kind: str
    label: str
    ref: str
    link: str | None = None


class ToolCallOut(BaseModel):
    tool: str
    arguments: dict[str, Any]
    ok: bool
    facts: list[str]
    duration_ms: int


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    intent: str | None
    sources: list[SourceOut]
    engine: str | None  # which engine wrote an answer
    sent_outside: bool  # whether the facts behind this answer were sent to an outside provider
    tool_calls: list[ToolCallOut]
    created_at: datetime


class AnswerOut(BaseModel):
    conversation_id: uuid.UUID
    title: str
    question: MessageOut
    answer: MessageOut


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    message_count: int
    last_message_at: datetime


class ConversationDetailOut(BaseModel):
    id: uuid.UUID
    title: str
    messages: list[MessageOut]  # oldest first


class AiSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_enabled: bool = True
    allow_external_ai: bool = False
    allow_model_improvement: bool = False


class AiSettingsOut(BaseModel):
    ai_enabled: bool
    allow_external_ai: bool  # may the facts behind an answer be sent to an outside provider
    allow_model_improvement: bool  # may conversations be used to improve models (never at present)
    external_provider_available: bool  # whether an outside provider is set up at all
    engine: str  # what answers now: the plain engine or the outside provider's name
    updated_at: datetime | None
