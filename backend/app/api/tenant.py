import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_owned(db: AsyncSession, model, obj_id: uuid.UUID, tenant_id: uuid.UUID, *options):
    """Fetch an object by id ONLY inside the caller's tenant.

    Returns 404 (not 403) for another tenant's object, so ids can't be probed.
    """
    stmt = select(model).where(model.id == obj_id, model.tenant_id == tenant_id)
    if options:
        stmt = stmt.options(*options)
    obj = await db.scalar(stmt)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj