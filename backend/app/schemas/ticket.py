import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TicketOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    title: str
    priority: str
    status: str
    reason: str | None
    assigned_to: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved"] | None = None
    assigned_to: uuid.UUID | None = None
