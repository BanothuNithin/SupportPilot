import json
import logging
import uuid
from collections.abc import AsyncGenerator

from anyio import CancelScope, get_cancelled_exc_class
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Send

from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import SessionLocal, get_db
from app.llm import get_llm
from app.llm.base import LLMError, LLMProvider
from app.models import Conversation, Message, User
from app.rag import prompt as rag_prompt
from app.rag.retrieve import retrieve
from app.schemas.message import MessageIn

router = APIRouter(prefix="/conversations", tags=["chat"])
log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are SupportPilot, a helpful customer support assistant. "
    "Answer clearly and briefly. If you do not know the answer, say so honestly."
)
HISTORY_LIMIT = 20


class DisconnectAwareStreamingResponse(StreamingResponse):
    def __init__(self, content: AsyncGenerator[str, None], headers: dict[str, str]):
        self.content_generator = content
        super().__init__(content, media_type="text/event-stream", headers=headers)

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        except OSError:
            with CancelScope(shield=True):
                await self.content_generator.aclose()
            raise
        except get_cancelled_exc_class():
            with CancelScope(shield=True):
                await self.content_generator.aclose()
            raise


def sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    conv = await get_owned(db, Conversation, conversation_id, user.tenant_id)
    tenant_id, conv_id = conv.tenant_id, conv.id  # plain values, safe to use after the request session closes

    db.add(Message(tenant_id=tenant_id, conversation_id=conv_id, sender="customer", content=body.content))
    await db.commit()

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
    hits = await retrieve(tenant_id, body.content)
    cites = rag_prompt.citations(hits)
    llm_messages = [{"role": "system", "content": rag_prompt.system_prompt(hits)}, *history]

    async def save_answer(answer: str) -> str:
        async with SessionLocal() as session:
            msg = Message(
                tenant_id=tenant_id,
                conversation_id=conv_id,
                sender="ai",
                content=answer,
                citations=json.dumps(cites) if cites else None,
            )
            session.add(msg)
            await session.commit()
            return str(msg.id)

    async def event_stream():
        parts: list[str] = []
        saved = False
        if cites:
            yield sse("citations", {"items": cites})

        async def save_partial() -> None:
            nonlocal saved
            answer = "".join(parts).strip()
            if answer and not saved:
                with CancelScope(shield=True):
                    await save_answer(answer)
                saved = True

        try:
            async for token in llm.stream(llm_messages):
                parts.append(token)
                yield sse("token", {"text": token})
            answer = "".join(parts).strip()
            if not answer:
                yield sse("error", {"detail": "The model returned an empty answer."})
                return
            with CancelScope(shield=True):
                message_id = await save_answer(answer)
            saved = True
            yield sse("done", {"message_id": message_id})
        except get_cancelled_exc_class():
            await save_partial()
            raise
        except GeneratorExit:
            await save_partial()
            raise
        except LLMError as e:
            yield sse("error", {"detail": str(e)})
            return
        except Exception:
            log.exception("LLM streaming failed")
            yield sse("error", {"detail": "The AI request failed."})
            return

    return DisconnectAwareStreamingResponse(
        event_stream(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
