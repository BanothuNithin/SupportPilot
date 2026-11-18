import uuid
from datetime import datetime

from pydantic import BaseModel


class ConversationCreate(BaseModel):
    customer_id: uuid.UUID


class MessageOut(BaseModel):
    id: uuid.UUID
    sender: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]