import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth, chat, conversations, customers
from app.api.routes import documents, tickets, integrations, actions
from app.core import redis as redis_module
from app.db.session import SessionLocal
from app.rag import store
from app.storage import get_storage

import app.models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

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


async def _bounded_check(check) -> bool:
    try:
        return await asyncio.wait_for(check(), timeout=2)
    except Exception:
        return False


@app.get("/health/ready")
async def health_ready():
    check_names = ("postgres", "redis", "qdrant", "storage")
    check_functions = (_postgres_ready, _redis_ready, _qdrant_ready, _storage_ready)
    results = await asyncio.gather(
        *(_bounded_check(check) for check in check_functions)
    )
    checks = dict(zip(check_names, results))
    ready = all(checks.values())
    return JSONResponse(
        content={"status": "ok" if ready else "error", "checks": checks},
        status_code=200 if ready else 503,
    )