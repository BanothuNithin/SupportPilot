import asyncio
import json
import logging
from time import perf_counter
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Send

from app.agents import prompts
from app.actions.proposals import build_bug_report_actions
from app.integrations.service import is_configured
from app.realtime import publish_message
from app.agents.graph import graph
from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.db.session import SessionLocal, get_db
from app.db.tenant import set_tenant
from app.llm import get_llm
from app.llm.base import LLMError, LLMProvider
from app.models import Conversation, Document, Message, PendingAction, Ticket, User
from app.schemas.message import MessageIn

router = APIRouter(prefix="/conversations", tags=["chat"])
log = logging.getLogger(__name__)

SAVE_TIMEOUT_SECONDS = 5


class DisconnectAwareStreamingResponse(StreamingResponse):
    def __init__(self, content: AsyncGenerator[str, None], headers: dict[str, str]):
        self.content_generator = content
        super().__init__(content, media_type="text/event-stream", headers=headers)

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        except OSError:
            with anyio.CancelScope(shield=True):
                await self.content_generator.aclose()
            raise
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                await self.content_generator.aclose()
            raise


def sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def decision_payload(result: dict) -> dict:
    grade = result.get("grade")
    return {
        "outcome": result.get("outcome"),
        "reason": result.get("reason"),
        "can_answer": grade.can_answer if grade is not None else None,
        "confidence": grade.confidence if grade is not None else None,
        "top_scores": [round(hit.score, 3) for hit in result.get("hits", [])],
        "search_query": result.get("search_query"),
    }


def log_decision(decision: dict) -> None:
    log.info(
        "decision %s",
        {key: value for key, value in decision.items() if key != "search_query"},
    )


async def persist_reply(
    tenant_id: uuid.UUID,
    conv_id: uuid.UUID,
    question: str,
    text: str,
    cites: list[dict],
    outcome: str | None,
    reason: str | None,
    priority: str,
    history: list[dict] | None = None,
) -> tuple[str, str | None, datetime]:
    """Save the AI reply and escalation state in one transaction."""
    async with SessionLocal() as session:
        await set_tenant(session, tenant_id)
        msg = Message(
            tenant_id=tenant_id,
            conversation_id=conv_id,
            sender="ai",
            content=text,
            citations=json.dumps(cites) if cites else None,
        )
        session.add(msg)
        ticket_id = None
        if outcome == "escalate":
            ticket = Ticket(
                tenant_id=tenant_id,
                conversation_id=conv_id,
                title=f"Escalated: {question[:100]}",
                priority=priority,
                status="open",
                reason=reason,
            )
            session.add(ticket)
            conv = await session.get(Conversation, conv_id)
            if conv is None:
                raise RuntimeError(f"Conversation {conv_id} disappeared before escalation save")
            conv.status = "escalated"
            await session.flush()
            ticket_id = str(ticket.id)
            if reason == "bug_report":
                for tool, arguments in build_bug_report_actions(
                    question,
                    history or [],
                    ticket.id,
                    conv_id,
                    priority,
                ):
                    spec_kind = "github" if tool == "create_github_issue" else "slack"
                    if not await is_configured(session, tenant_id, spec_kind):
                        continue
                    open_action = await session.scalar(
                        select(PendingAction.id)
                        .where(
                            PendingAction.tenant_id == tenant_id,
                            PendingAction.conversation_id == conv_id,
                            PendingAction.tool == tool,
                            PendingAction.status.in_(["pending", "approved"]),
                        )
                        .limit(1)
                    )
                    if open_action is not None:
                        continue
                    session.add(PendingAction(
                        tenant_id=tenant_id,
                        conversation_id=conv_id,
                        ticket_id=ticket.id,
                        tool=tool,
                        arguments=json.dumps(arguments),
                        status="pending",
                    ))
        await session.commit()
        await session.refresh(msg)
        return str(msg.id), ticket_id, msg.created_at


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    conv = await get_owned(db, Conversation, conversation_id, user.tenant_id)
    await rate_limit(
        f"rl:chat:user:{user.id}",
        limit=settings.chat_rate_limit_user_per_minute,
        window_seconds=60,
        fail_open=True,
    )
    await rate_limit(
        f"rl:chat:tenant:{user.tenant_id}",
        limit=settings.chat_rate_limit_tenant_per_minute,
        window_seconds=60,
        fail_open=True,
    )
    tenant_id, conv_id = conv.tenant_id, conv.id
    was_escalated = conv.status == "escalated"
    if conv.status == "resolved":
        conv.status = "ai_handling"
    customer_message = Message(
        tenant_id=tenant_id,
        conversation_id=conv_id,
        sender="customer",
        content=body.content,
    )
    db.add(customer_message)
    await db.commit()
    await db.refresh(customer_message)
    if not was_escalated:
        await publish_message(
            tenant_id,
            conv_id,
            {
                "type": "message",
                "message": {
                    "id": str(customer_message.id),
                    "sender": "customer",
                    "content": customer_message.content,
                    "author_id": None,
                    "created_at": str(customer_message.created_at),
                },
            },
        )

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if was_escalated:
        open_ticket = await db.scalar(
            select(Ticket.id)
            .where(Ticket.conversation_id == conv_id, Ticket.status != "resolved")
            .order_by(Ticket.created_at.desc())
        )
        await publish_message(
            tenant_id,
            conv_id,
            {
                "type": "message",
                "message": {
                    "id": str(customer_message.id),
                    "sender": "customer",
                    "content": customer_message.content,
                    "author_id": None,
                    "created_at": str(customer_message.created_at),
                },
            },
        )

        async def waiting():
            yield sse("escalated", {"ticket_id": str(open_ticket) if open_ticket else None, "waiting": True})
            yield sse("done", {"message_id": None})

        return StreamingResponse(waiting(), media_type="text/event-stream", headers=headers)

    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.tenant_id == tenant_id)
        .order_by(Message.created_at.desc())
        .limit(settings.history_limit * 2)
    )
    handoff_messages = set(prompts.HANDOFF.values())
    history = [
        {"role": "user" if m.sender == "customer" else "assistant", "content": m.content}
        for m in reversed(list(rows))
        if not (m.sender == "ai" and m.content in handoff_messages)
    ]
    history = history[-settings.history_limit :]
    generation_rows = await db.execute(
        select(Document.id, Document.active_generation).where(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
        )
    )
    active_generations = {
        row.id: row.active_generation for row in generation_rows.all()
    }
    initial = {
        "question": body.content,
        "history": history,
        "tenant_id": tenant_id,
        "active_generations": active_generations,
    }

    async def event_stream():
        started_at = perf_counter()
        last_update_at = started_at
        parts: list[str] = []
        result: dict = {}
        cites: list[dict] = []
        stage_ms: dict[str, int] = {}
        ttft_ms: int | None = None
        decision_emitted = False
        saved = failed = False
        cancel = asyncio.Event()
        try:
            async for mode, chunk in graph.astream(
                initial,
                config={"configurable": {"llm": llm, "cancel": cancel}},
                stream_mode=["updates", "custom"],
            ):
                if mode == "custom":
                    if chunk["type"] == "token":
                        if ttft_ms is None:
                            ttft_ms = int((perf_counter() - started_at) * 1000)
                            log.info(
                                "first token after %s ms, outcome=%s",
                                ttft_ms,
                                result.get("outcome"),
                            )
                        parts.append(chunk["text"])
                        yield sse("token", {"text": chunk["text"]})
                    elif chunk["type"] == "citations":
                        cites = chunk["items"]
                        yield sse("citations", {"items": cites})
                    continue
                for node, update in chunk.items():
                    result.update(update or {})
                    update_at = perf_counter()
                    stage_ms[node] = max(0, int((update_at - last_update_at) * 1000))
                    last_update_at = update_at
                    yield sse("step", {"name": node})
                    if node == "triage":
                        triage = result["triage"]
                        yield sse("triage", {"intent": triage.intent, "priority": triage.priority})
                        if "outcome" in (update or {}) and not decision_emitted:
                            decision = decision_payload(result)
                            log_decision(decision)
                            decision_emitted = True
                            yield sse("decision", decision)
                    elif node == "decide" and not decision_emitted:
                        decision = decision_payload(result)
                        log_decision(decision)
                        decision_emitted = True
                        yield sse("decision", decision)

            answer = "".join(parts).strip()
            if not answer:
                failed = True
                yield sse("error", {"detail": "The model returned an empty answer."})
                return
            with anyio.CancelScope(shield=True):
                message_id, ticket_id, message_created_at = await persist_reply(
                    tenant_id,
                    conv_id,
                    body.content,
                    answer,
                    cites,
                    result.get("outcome"),
                    result.get("reason"),
                    result["triage"].priority,
                    history,
                )
            await publish_message(
                tenant_id,
                conv_id,
                {
                    "type": "message",
                    "message": {
                        "id": message_id,
                        "sender": "ai",
                        "content": answer,
                        "author_id": None,
                        "created_at": str(message_created_at),
                    },
                },
            )
            saved = True
            if result.get("outcome") == "escalate":
                yield sse("escalated", {"ticket_id": ticket_id, "reason": result.get("reason")})
            yield sse(
                "done",
                {"message_id": message_id, "ttft_ms": ttft_ms, "stage_ms": stage_ms},
            )
        except LLMError as e:
            failed = True
            yield sse("error", {"detail": str(e)})
        except Exception:
            failed = True
            log.exception("Agent run failed")
            yield sse("error", {"detail": "The AI request failed."})
        finally:
            cancel.set()
            if parts and not saved and not failed:
                with anyio.move_on_after(SAVE_TIMEOUT_SECONDS, shield=True):
                    await persist_reply(
                        tenant_id,
                        conv_id,
                        body.content,
                        "".join(parts).strip(),
                        cites,
                        None,
                        None,
                        "medium",
                    )

    return DisconnectAwareStreamingResponse(event_stream(), headers=headers)
