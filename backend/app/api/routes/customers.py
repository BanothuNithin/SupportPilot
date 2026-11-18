import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.tenant import get_owned
from app.db.session import get_db
from app.models import Conversation, Customer, Role, User
from app.schemas.customer import CustomerCreate, CustomerOut, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    customer = Customer(tenant_id=user.tenant_id, **body.model_dump())
    db.add(customer)
    await db.commit()
    return customer


@router.get("", response_model=list[CustomerOut])
async def list_customers(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Customer).where(Customer.tenant_id == user.tenant_id))
    return list(rows)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await get_owned(db, Customer, customer_id, user.tenant_id)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    customer = await get_owned(db, Customer, customer_id, user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await db.commit()
    return customer


@router.delete("/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: uuid.UUID,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    customer = await get_owned(db, Customer, customer_id, user.tenant_id)
    count = await db.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id))
    if count:
        raise HTTPException(status.HTTP_409_CONFLICT, "Customer has conversations")
    await db.delete(customer)
    await db.commit()