import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import get_db
from app.models import Conversation, Customer, User
from app.schemas.conversation import ConversationCreate, ConversationDetail, ConversationOut

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