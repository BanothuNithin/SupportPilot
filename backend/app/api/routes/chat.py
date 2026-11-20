import json
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import SessionLocal, get_db
from llm import get_llm
from llm.base import LLMError, LLMProvider
from app.models import Conversation, Message, User
from app.schemas.message import MessageIn

router = APIRouter(prefix="/conversations", tags=["chat"])
log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are SupportPilot, a helpful customer support assistant. "
    "Answer clearly and briefly. If you do not know the answer, say so honestly."
)
HISTORY_LIMIT = 20


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
    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    async def event_stream():
        parts: list[str] = []
        try:
            async for token in llm.stream(llm_messages):
                parts.append(token)
                yield sse("token", {"text": token})
        except LLMError as e:
            yield sse("error", {"detail": str(e)})
            return
        except Exception:
            log.exception("LLM streaming failed")
            yield sse("error", {"detail": "The AI request failed."})
            return

        answer = "".join(parts).strip()
        if not answer:
            yield sse("error", {"detail": "The model returned an empty answer."})
            return
        # The request-scoped session may be closed by now, so the stream opens its own.
        async with SessionLocal() as session:
            msg = Message(tenant_id=tenant_id, conversation_id=conv_id, sender="ai", content=answer)
            session.add(msg)
            await session.commit()
            message_id = str(msg.id)
        yield sse("done", {"message_id": message_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
