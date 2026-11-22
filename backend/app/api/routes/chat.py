import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Send

from app.agents.graph import graph
from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import SessionLocal, get_db
from app.llm import get_llm
from app.llm.base import LLMError, LLMProvider
from app.models import Conversation, Message, Ticket, User
from app.schemas.message import MessageIn

router = APIRouter(prefix="/conversations", tags=["chat"])
log = logging.getLogger(__name__)

HISTORY_LIMIT = 20
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


async def persist_reply(
    tenant_id: uuid.UUID,
    conv_id: uuid.UUID,
    question: str,
    text: str,
    cites: list[dict],
    outcome: str | None,
    reason: str | None,
    priority: str,
) -> tuple[str, str | None]:
    """Save the AI reply and escalation state in one transaction."""
    async with SessionLocal() as session:
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
        await session.commit()
        return str(msg.id), ticket_id


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    conv = await get_owned(db, Conversation, conversation_id, user.tenant_id)
    tenant_id, conv_id = conv.tenant_id, conv.id
    was_escalated = conv.status == "escalated"
    if conv.status == "resolved":
        conv.status = "ai_handling"
    db.add(Message(tenant_id=tenant_id, conversation_id=conv_id, sender="customer", content=body.content))
    await db.commit()

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if was_escalated:
        open_ticket = await db.scalar(
            select(Ticket.id)
            .where(Ticket.conversation_id == conv_id, Ticket.status != "resolved")
            .order_by(Ticket.created_at.desc())
        )

        async def waiting():
            yield sse("escalated", {"ticket_id": str(open_ticket) if open_ticket else None, "waiting": True})
            yield sse("done", {"message_id": None})

        return StreamingResponse(waiting(), media_type="text/event-stream", headers=headers)

    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.tenant_id == tenant_id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = [
        {"role": "user" if m.sender == "customer" else "assistant", "content": m.content}
        for m in reversed(list(rows))
    ]
    initial = {"question": body.content, "history": history, "tenant_id": tenant_id}

    async def event_stream():
        parts: list[str] = []
        result: dict = {}
        cites: list[dict] = []
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
                        parts.append(chunk["text"])
                        yield sse("token", {"text": chunk["text"]})
                    elif chunk["type"] == "citations":
                        cites = chunk["items"]
                        yield sse("citations", {"items": cites})
                    continue
                for node, update in chunk.items():
                    result.update(update or {})
                    yield sse("step", {"name": node})
                    if node == "triage":
                        triage = result["triage"]
                        yield sse("triage", {"intent": triage.intent, "priority": triage.priority})

            answer = "".join(parts).strip()
            if not answer:
                failed = True
                yield sse("error", {"detail": "The model returned an empty answer."})
                return
            with anyio.CancelScope(shield=True):
                message_id, ticket_id = await persist_reply(
                    tenant_id,
                    conv_id,
                    body.content,
                    answer,
                    cites,
                    result.get("outcome"),
                    result.get("reason"),
                    result["triage"].priority,
                )
            saved = True
            if result.get("outcome") == "escalate":
                yield sse("escalated", {"ticket_id": ticket_id, "reason": result.get("reason")})
            yield sse("done", {"message_id": message_id})
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
