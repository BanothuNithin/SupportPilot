from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant

_SET_CONTEXT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def _apply_context(session: AsyncSession) -> None:
    await session.execute(
        _SET_CONTEXT,
        {"tenant_id": str(session.info.get("tenant_id") or "")},
    )


async def set_tenant(session: AsyncSession, tenant_id: UUID | str) -> None:
    if not _is_postgres(session):
        return
    session.info["tenant_id"] = str(tenant_id)
    if session.in_transaction():
        await _apply_context(session)

async def worker_tenant_ids(session: AsyncSession) -> list[UUID]:
    if _is_postgres(session):
        result = await session.scalars(
            text("SELECT public.worker_tenant_ids() AS tenant_id")
        )
        return list(result.all())
    result = await session.scalars(select(Tenant.id).order_by(Tenant.id))
    return list(result.all())
