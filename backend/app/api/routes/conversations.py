import uuid
from contextlib import aclosing


from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import get_db
from app.models import Conversation, Customer, Message, Ticket, User
from app.realtime import conversation_events, publish_message
from app.schemas.conversation import ConversationCreate, ConversationDetail, ConversationOut, HumanReplyIn, MessageOut

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # The customer must belong to the caller's tenant (404 otherwise).
    await get_owned(db, Customer, body.customer_id, user.tenant_id)
    conv = Conversation(tenant_id=user.tenant_id, customer_id=body.customer_id)
    db.add(conv)
    await db.commit()
    return conv


@router.get("", response_model=list[ConversationOut])
async def list_conversations(status: str | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Conversation).where(Conversation.tenant_id == user.tenant_id)
    if status:
        stmt = stmt.where(Conversation.status == status)
    return list(await db.scalars(stmt.order_by(Conversation.created_at.desc())))


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # selectinload fetches all messages in ONE extra query (avoids N+1).
    return await get_owned(db, Conversation, conversation_id, user.tenant_id, selectinload(Conversation.messages))


@router.get("/{conversation_id}/events")
async def get_events(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned(db, Conversation, conversation_id, user.tenant_id)
    tenant_id = user.tenant_id
    conv_id = conv.id
    await db.close()

    async def event_stream():
        async with aclosing(
            conversation_events(tenant_id, conv_id)
        ) as events:
            async for event in events:
                yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/human-reply", response_model=MessageOut)
async def human_reply(
    conversation_id: uuid.UUID,
    body: HumanReplyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned(db, Conversation, conversation_id, user.tenant_id)
    if conv.status != "escalated":
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversation is not escalated")

    message = Message(
        tenant_id=user.tenant_id,
        conversation_id=conv.id,
        sender="human",
        author_id=user.id,
        content=body.content,
    )
    db.add(message)
    await db.flush()

    ticket = await db.scalar(
        select(Ticket)
        .where(
            Ticket.conversation_id == conv.id,
            Ticket.status != "resolved",
        )
        .order_by(Ticket.created_at.desc())
    )
    if ticket is not None and ticket.status == "open":
        ticket.status = "in_progress"
    if ticket is not None and ticket.assigned_to is None:
        ticket.assigned_to = user.id

    await db.commit()
    await db.refresh(message)

    await publish_message(
        user.tenant_id,
        conv.id,
        {
            "type": "message",
            "message": {
                "id": str(message.id),
                "sender": "human",
                "content": message.content,
                "author_id": str(message.author_id) if message.author_id else None,
                "created_at": str(message.created_at),
            },
        },
    )
    return message