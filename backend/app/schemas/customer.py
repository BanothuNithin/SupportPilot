import uuid

from pydantic import BaseModel, EmailStr


class CustomerCreate(BaseModel):
    external_id: str | None = None
    email: EmailStr | None = None


class CustomerUpdate(BaseModel):
    external_id: str | None = None
    email: EmailStr | None = None


class CustomerOut(BaseModel):
    id: uuid.UUID
    external_id: str | None
    email: str | None

    model_config = {"from_attributes": True}