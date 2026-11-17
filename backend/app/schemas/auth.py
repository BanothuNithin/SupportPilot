import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models.core import Role


class RegisterIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: Role = Role.agent


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: Role

    model_config = {"from_attributes": True}
