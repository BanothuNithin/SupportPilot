import json
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    customer_id: uuid.UUID


class HumanReplyIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: uuid.UUID
    sender: str
    content: str
    author_id: uuid.UUID | None = None
    citations: list[dict] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("citations", mode="before")
    @classmethod
    def _parse_citations(cls, v):
        return json.loads(v) if isinstance(v, str) else v


class ConversationOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]