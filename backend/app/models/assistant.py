"""The AI assistant (Phase 13): conversations with the business's own figures.

The assistant never answers from a language model's memory. A question is understood by rules,
answered by looking up the business's own results (the "tools"), and the answer is made from what
those tools returned. Everything that happened is kept so it can be read back and checked:

- conversations and their messages (and which sources each answer came from)
- the context carried between questions, so "and why?" knows what "it" is
- every tool call: what was asked of which part of the system and what came back
- which answering engine and version wrote each answer
- the business's own controls over AI (switched on or off, whether anything may leave the system)
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.identity import _one_of

PROVIDERS = ("offline", "anthropic")
MESSAGE_ROLES = ("user", "assistant")


class AiModelVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Which engine (and which version of it) wrote an answer. Shared by every business."""

    __tablename__ = "ai_model_versions"
    __table_args__ = (
        UniqueConstraint("provider", "model", "version", name="uq_ai_model_versions_key"),
        CheckConstraint(_one_of("provider", PROVIDERS), name="provider_valid"),
    )

    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class AiSettings(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """The business's own controls over AI. Sending anything to an outside provider, and the use of
    conversations to improve models, are both off until the owner turns them on."""

    __tablename__ = "ai_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_ai_settings_organization"),)

    ai_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    allow_external_ai: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    allow_model_improvement: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Conversation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_conversations_org_id"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        Index("ix_conversations_org_user", "organization_id", "user_id", "last_message_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationMessage(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_conversation_messages_org_id"),
        CheckConstraint(_one_of("role", MESSAGE_ROLES), name="role_valid"),
        Index("ix_conversation_messages_conversation", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column()
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(30))
    # Where each statement in an answer came from: [{"kind": "kpi", "label": "...", "ref": "..."}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_model_versions.id"))
    sent_outside: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationContext(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """What the conversation is about right now, so a follow-up question can leave things out."""

    __tablename__ = "conversation_context"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", name="uq_conversation_context_conversation"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class AiToolCall(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One look-up the assistant made to answer: which, with what, and what came back."""

    __tablename__ = "ai_tool_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "message_id"],
            ["conversation_messages.organization_id", "conversation_messages.id"],
            ondelete="CASCADE",
        ),
        Index("ix_ai_tool_calls_message", "message_id"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column()
    tool: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    ok: Mapped[bool] = mapped_column(Boolean)
    facts: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    duration_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
