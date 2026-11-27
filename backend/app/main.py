import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth, chat, conversations, customers
from app.api.routes import documents, tickets, integrations, actions
from app.core import redis as redis_module
from app.db.session import SessionLocal, engine
from app.rag import store
from app.storage import get_storage

import app.models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="SupportPilot API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(tickets.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(actions.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


async def verify_rls_database_role() -> None:
    if engine.dialect.name != "postgresql":
        return
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT current_user, role.rolsuper, role.rolbypassrls, "
                        "EXISTS (SELECT 1 FROM pg_class AS cls "
                        "JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace "
                        "WHERE ns.nspname = 'public' "
                        "AND cls.relname IN "
                        "('tenants', 'users', 'customers', 'conversations', "
                        "'messages', 'tickets', 'documents', 'integrations', "
                        "'pending_actions', 'document_index_generations') "
                        "AND pg_get_userbyid(cls.relowner) = current_user) "
                        "AS owns_tenant_table "
                        "FROM pg_roles AS role WHERE role.rolname = current_user"
                    )
                )
            ).one_or_none()
    except Exception:
        log.error("RLS role safety check failed; current database role is unverified")
        return
    if (
        row is None
        or row.rolsuper
        or row.rolbypassrls
        or row.owns_tenant_table
    ):
        role_name = row.current_user if row is not None else "unknown"
        log.error(
            "UNSAFE DATABASE ROLE %s: owner, superuser or BYPASSRLS privileges disable tenant RLS",
            role_name,
        )


app.router.on_startup.append(verify_rls_database_role)


async def _postgres_ready() -> bool:
    async with SessionLocal() as db:
        await db.execute(text("SELECT 1"))
    return True


async def _redis_ready() -> bool:
    return bool(await redis_module.redis_client.ping())


async def _qdrant_ready() -> bool:
    await store.client.get_collections()
    return True


async def _storage_ready() -> bool:
    return bool(await get_storage().ping())


async def _rls_enforced() -> bool:
    if engine.dialect.name != "postgresql":
        return True
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT role.rolsuper, role.rolbypassrls, "
                    "EXISTS (SELECT 1 FROM pg_class AS cls "
                    "JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace "
                    "WHERE ns.nspname = 'public' "
                    "AND cls.relname IN "
                    "('tenants', 'users', 'customers', 'conversations', "
                    "'messages', 'tickets', 'documents', 'integrations', "
                    "'pending_actions', 'document_index_generations') "
                    "AND pg_get_userbyid(cls.relowner) = current_user) "
                    "AS owns_tenant_table "
                    "FROM pg_roles AS role WHERE role.rolname = current_user"
                )
            )
        ).one_or_none()
    return (
        row is not None
        and not row.rolsuper
        and not row.rolbypassrls
        and not row.owns_tenant_table
    )


async def _bounded_check(check) -> bool:
    try:
        return await asyncio.wait_for(check(), timeout=2)
    except Exception:
        return False


@app.get("/health/ready")
async def health_ready():
    check_names = ("postgres", "redis", "qdrant", "storage", "rls_enforced")
    check_functions = (
        _postgres_ready,
        _redis_ready,
        _qdrant_ready,
        _storage_ready,
        _rls_enforced,
    )
    results = await asyncio.gather(
        *(_bounded_check(check) for check in check_functions)
    )
    checks = dict(zip(check_names, results))
    ready = all(checks.values())
    return JSONResponse(
        content={"status": "ok" if ready else "error", "checks": checks},
        status_code=200 if ready else 503,
    )