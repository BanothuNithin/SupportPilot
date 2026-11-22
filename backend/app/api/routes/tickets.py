import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.tenant import get_owned
from app.db.session import get_db
from app.models import Conversation, Ticket, User
from app.schemas.ticket import TicketOut, TicketUpdate

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=list[TicketOut])
async def list_tickets(status: str | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Ticket).where(Ticket.tenant_id == user.tenant_id)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return list(await db.scalars(stmt.order_by(Ticket.created_at.desc())))


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: uuid.UUID, body: TicketUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    ticket = await get_owned(db, Ticket, ticket_id, user.tenant_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("assigned_to"):
        # the assignee must be staff of the SAME tenant
        await get_owned(db, User, data["assigned_to"], user.tenant_id)
    for field, value in data.items():
        setattr(ticket, field, value)
    if data.get("status") == "resolved":
        conv = await get_owned(db, Conversation, ticket.conversation_id, user.tenant_id)
        conv.status = "resolved"  # a new customer message reopens it for the AI
    await db.commit()
    return ticket
