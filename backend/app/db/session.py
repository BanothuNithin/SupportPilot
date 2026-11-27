import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine_options = {"poolclass": NullPool} if os.getenv("TEST_DB") == "postgres" else {}
engine = create_async_engine(settings.database_url, echo=False, **engine_options)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
worker_engine = (
    create_async_engine(
        settings.worker_database_url,
        echo=False,
        **engine_options,
    )
    if settings.worker_database_url
    else engine
)
WorkerSessionLocal = async_sessionmaker(worker_engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _set_transaction_tenant_context(session, transaction, connection):
    if connection.dialect.name != "postgresql":
        return
    result = connection.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true)"
        ),
        {
            "tenant_id": str(session.info.get("tenant_id") or ""),
        },
    )
    result.close()


async def get_db():
    async with SessionLocal() as session:
        yield session
