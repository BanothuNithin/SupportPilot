import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["RAG_MIN_SCORE"] = "0.35"
os.environ["MIN_CONFIDENCE"] = "0.5"
os.environ["RAG_TOP_K"] = "4"
os.environ["CHAT_RATE_LIMIT_USER_PER_MINUTE"] = "20"
os.environ["CHAT_RATE_LIMIT_TENANT_PER_MINUTE"] = "120"
os.environ["EMBED_CACHE_TTL_SECONDS"] = "3600"
os.environ["LLM_SMALL_MODEL"] = ""
os.environ["LLM_FALLBACK_MODEL"] = ""
os.environ["INTEGRATIONS_KEY"] = "wvyzx2-IJJG-H42krUEt_EDr8IJhsVQ23o_gdCwEeNU="
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBED_PROVIDER"] = "fake"
os.environ["QDRANT_URL"] = ":memory:"
os.environ["S3_ENDPOINT"] = ""
os.environ["S3_ACCESS_KEY"] = "test-access"
os.environ["S3_SECRET_KEY"] = "test-secret"
os.environ["S3_BUCKET"] = "supportpilot-test"
os.environ["S3_REGION"] = "us-east-1"
os.environ["JOB_MAX_ATTEMPTS"] = "3"
os.environ["JOB_LEASE_SECONDS"] = "300"
os.environ["JOB_REQUEUE_AFTER_SECONDS"] = "1"
os.environ["RECONCILE_INTERVAL_SECONDS"] = "30"
os.environ["WORKER_CONCURRENCY"] = "4"

import fakeredis.aioredis  # noqa: E402
import app.core.redis as redis_mod  # noqa: E402
import app.storage as storage_mod  # noqa: E402


import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

@pytest_asyncio.fixture(autouse=True)
async def fake_redis(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", fakeredis.aioredis.FakeRedis(decode_responses=True))


@pytest_asyncio.fixture(autouse=True)
async def fake_storage(monkeypatch):
    from app.storage import FakeStorage

    storage = FakeStorage()
    monkeypatch.setattr(storage_mod, "_storage", storage)
    yield storage


@pytest_asyncio.fixture
async def drain_jobs():
    async def drain():
        from app.jobs.queue import dequeue
        from app.worker import handlers

        count = 0
        while True:
            job = await dequeue(1)
            if job is None:
                return count
            handler = handlers.get(job.get("type"))
            if handler is not None:
                await handler(job["id"], job["tenant_id"])
                count += 1

    return drain