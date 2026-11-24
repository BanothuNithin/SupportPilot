import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


class ActionApproveIn(BaseModel):
    arguments: dict[str, Any] | None = None


class ActionOut(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID | None
    conversation_id: uuid.UUID
    tool: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    error: str | None
    edited: bool
    created_at: datetime
    decided_at: datetime | None

    model_config = {"from_attributes": True}

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments(cls, value):
        if isinstance(value, str):
            return json.loads(value)
        return value

    @field_validator("result", mode="before")
    @classmethod
    def parse_result(cls, value):
        if isinstance(value, str):
            return json.loads(value) if value else None
        return value
