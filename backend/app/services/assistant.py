# ruff: noqa: E501
"""The assistant: a question in, an answer out, made only from the business's own results.

The steps are always the same: check the business allows it; understand the question by rules; look
up what it needs (the tools); build the plain answer from what was found; if, and only if, the owner
has allowed an outside provider, let it reword that answer, and keep its wording only if every
figure in it is one that was looked up; write down the answer, where each part came from and every
look-up made. A question that is not understood, or finds nothing, gets an honest "I do not know".
"""

import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import answers, guard, intents
from app.ai.provider import OFFLINE, Engine, Provider, ProviderError
from app.core.config import get_settings
from app.core.errors import NotFoundError, PermissionDeniedError
from app.integrations.base import utcnow
from app.models.assistant import (
    AiModelVersion,
    AiSettings,
    AiToolCall,
    Conversation,
    ConversationContext,
    ConversationMessage,
)
from app.schemas.assistant import (
    AiSettingsIn,
    AiSettingsOut,
    AnswerOut,
    AskIn,
    ConversationDetailOut,
    ConversationOut,
    MessageOut,
    SourceOut,
    ToolCallOut,
)
from app.services import assistant_tools as tools
from app.services.audit import AuditAction, record_audit
from app.services.auth import RequestMeta

logger = logging.getLogger("vyterlix.assistant")

TITLE_CHARS = 60
KEEP_CONTEXT = ("intent", "kpi_code", "month", "event_id")


# --- the business's own controls ------------------------------------------------------------------------


def _settings_row(db: Session) -> AiSettings | None:
    return db.scalars(select(AiSettings)).first()


def settings_out(db: Session, provider: Provider | None) -> AiSettingsOut:
    row = _settings_row(db)
    available = provider is not None
    allowed = row is not None and row.allow_external_ai
    return AiSettingsOut(
        ai_enabled=True if row is None else row.ai_enabled,
        allow_external_ai=False if row is None else row.allow_external_ai,
        allow_model_improvement=False if row is None else row.allow_model_improvement,
        external_provider_available=available,
        engine=provider.engine.model if available and allowed else OFFLINE.model,
        updated_at=None if row is None else row.updated_at,
    )


def set_settings(
    db: Session, tenant, body: AiSettingsIn, provider: Provider | None, meta: RequestMeta
) -> AiSettingsOut:
    row = _settings_row(db)
    if row is None:
        row = AiSettings(organization_id=tenant.organization_id)
        db.add(row)
    before = (
        (row.ai_enabled, row.allow_external_ai, row.allow_model_improvement) if row.id else None
    )
    row.ai_enabled, row.allow_external_ai, row.allow_model_improvement = (
        body.ai_enabled,
        body.allow_external_ai,
        body.allow_model_improvement,
    )
    row.updated_by_user_id = tenant.user.id
    db.flush()
    record_audit(
        db, AuditAction.ASSISTANT_SETTINGS_CHANGED, actor_user_id=tenant.user.id,
        organization_id=tenant.organization_id, target_type="assistant", target_id=row.id,
        ip_address=meta.ip_address, user_agent=meta.user_agent,
        details={"ai_enabled": body.ai_enabled, "allow_external_ai": body.allow_external_ai,
                 "allow_model_improvement": body.allow_model_improvement, "before": before},
    )  # fmt: skip
    db.commit()
    return settings_out(db, provider)


def configured_provider() -> Provider | None:
    """The outside provider that is set up for this installation, if any."""
    settings = get_settings()
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key is not None:
        from app.ai.provider import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(), settings.anthropic_model,
            timeout=settings.ai_timeout_seconds,
        )  # fmt: skip
    return None


# --- the engine on record ---------------------------------------------------------------------------------


def _engine_row(db: Session, engine: Engine) -> AiModelVersion:
    row = db.scalars(
        select(AiModelVersion).where(
            AiModelVersion.provider == engine.provider,
            AiModelVersion.model == engine.model,
            AiModelVersion.version == engine.version,
        )
    ).first()
    if row is None:
        row = AiModelVersion(
            provider=engine.provider,
            model=engine.model,
            version=engine.version,
            description=engine.description,
        )
        db.add(row)
        db.flush()
    return row


# --- reading ---------------------------------------------------------------------------------------------


def _message_out(m: ConversationMessage, engines: dict, calls: list[AiToolCall]) -> MessageOut:
    engine = engines.get(m.model_version_id)
    return MessageOut(
        id=m.id, role=m.role, content=m.content, intent=m.intent,
        sources=[SourceOut(**s) for s in m.sources], engine=None if engine is None else engine.model,
        sent_outside=m.sent_outside,
        tool_calls=[ToolCallOut(tool=c.tool, arguments=c.arguments, ok=c.ok, facts=c.facts, duration_ms=c.duration_ms) for c in calls],
        created_at=m.created_at,
    )  # fmt: skip


def _messages(db: Session, conversation_id: uuid.UUID) -> list[MessageOut]:
    rows = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    ).all()
    engines = {e.id: e for e in db.scalars(select(AiModelVersion))}
    calls: dict = {}
    for c in db.scalars(
        select(AiToolCall)
        .where(AiToolCall.message_id.in_([m.id for m in rows]))
        .order_by(AiToolCall.created_at)
    ):
        calls.setdefault(c.message_id, []).append(c)
    return [_message_out(m, engines, calls.get(m.id, [])) for m in rows]


def _mine(db: Session, tenant, conversation_id: uuid.UUID) -> Conversation:
    """A conversation belongs to the person who had it: nobody else in the business can read it."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != tenant.user.id:
        raise NotFoundError("That conversation was not found", code="conversation_not_found")
    return conversation


def list_conversations(db: Session, tenant) -> list[ConversationOut]:
    rows = db.execute(
        select(Conversation, func.count(ConversationMessage.id))
        .outerjoin(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
        .where(Conversation.user_id == tenant.user.id)
        .group_by(Conversation.id)
        .order_by(Conversation.last_message_at.desc())
    ).all()
    return [
        ConversationOut(id=c.id, title=c.title, message_count=n, last_message_at=c.last_message_at)
        for c, n in rows
    ]


def get_conversation(db: Session, tenant, conversation_id: uuid.UUID) -> ConversationDetailOut:
    conversation = _mine(db, tenant, conversation_id)
    return ConversationDetailOut(
        id=conversation.id, title=conversation.title, messages=_messages(db, conversation.id)
    )


def delete_conversation(db: Session, tenant, conversation_id: uuid.UUID) -> None:
    db.delete(_mine(db, tenant, conversation_id))
    db.commit()


# --- asking ----------------------------------------------------------------------------------------------


def _context(db: Session, conversation_id: uuid.UUID) -> ConversationContext | None:
    return db.scalars(
        select(ConversationContext).where(ConversationContext.conversation_id == conversation_id)
    ).first()


def ask(
    db: Session, tenant, body: AskIn, meta: RequestMeta, provider: Provider | None = None
) -> AnswerOut:
    row = _settings_row(db)
    if row is not None and not row.ai_enabled:
        raise PermissionDeniedError(answers.SWITCHED_OFF, code="ai_disabled")
    external = provider is not None and row is not None and row.allow_external_ai

    now = utcnow()
    if body.conversation_id is None:
        conversation = Conversation(
            organization_id=tenant.organization_id, user_id=tenant.user.id,
            title=body.message[:TITLE_CHARS], last_message_at=now,
        )  # fmt: skip
        db.add(conversation)
        db.flush()
    else:
        conversation = _mine(db, tenant, body.conversation_id)
    context = _context(db, conversation.id)

    question = ConversationMessage(
        organization_id=tenant.organization_id, conversation_id=conversation.id, role="user",
        content=body.message, sources=[], created_at=now,
    )  # fmt: skip
    db.add(question)
    db.flush()

    understood = intents.understand(
        body.message, latest=tools.latest_month(db), context=None if context is None else context.data,
        extra_words=tools.figure_words(db),
    )  # fmt: skip
    started = time.monotonic()
    results = tools.run(db, tenant, understood)
    elapsed = int((time.monotonic() - started) * 1000)

    facts = answers.all_facts(results)
    if understood.intent == "greeting":
        text = answers.GREETING
    elif understood.intent == "help":
        text = answers.HELP
    else:
        text = answers.plain_answer(understood.intent, results)
    engine, sent_outside = OFFLINE, False
    if external and facts:
        sent_outside = True  # the question and the facts leave the system from here
        try:
            reworded = provider.reword(body.message, facts)
            ok, why = guard.acceptable(reworded, facts)
            if ok:
                text, engine = reworded.strip(), provider.engine
            else:
                logger.warning("Outside answer rejected: %s", why)
        except ProviderError:
            logger.warning("Outside provider could not be used", exc_info=True)

    answer = ConversationMessage(
        organization_id=tenant.organization_id, conversation_id=conversation.id, role="assistant",
        content=text, intent=understood.intent, sources=answers.all_sources(results),
        model_version_id=_engine_row(db, engine).id, sent_outside=sent_outside,
        created_at=max(utcnow(), now + timedelta(microseconds=1)),
    )  # fmt: skip
    db.add(answer)
    db.flush()
    for r in results:
        db.add(
            AiToolCall(
                organization_id=tenant.organization_id, message_id=answer.id, tool=r.tool,
                arguments=r.arguments, ok=r.ok, facts=r.facts, duration_ms=elapsed, created_at=answer.created_at,
            )
        )  # fmt: skip

    carried = {k: v for r in results if r.ok for k, v in r.context.items() if k in KEEP_CONTEXT}
    if carried:
        if context is None:
            context = ConversationContext(
                organization_id=tenant.organization_id, conversation_id=conversation.id, data={}
            )
            db.add(context)
        context.data = {**context.data, **carried}
    conversation.last_message_at = answer.created_at
    if sent_outside:
        record_audit(
            db, AuditAction.ASSISTANT_SENT_OUTSIDE, actor_user_id=tenant.user.id,
            organization_id=tenant.organization_id, target_type="conversation", target_id=conversation.id,
            ip_address=meta.ip_address, user_agent=meta.user_agent,
            details={"provider": provider.engine.provider, "model": provider.engine.model, "facts": len(facts), "used": engine is not OFFLINE},
        )  # fmt: skip
    db.commit()
    messages = {m.id: m for m in _messages(db, conversation.id)}
    return AnswerOut(
        conversation_id=conversation.id,
        title=conversation.title,
        question=messages[question.id],
        answer=messages[answer.id],
    )
