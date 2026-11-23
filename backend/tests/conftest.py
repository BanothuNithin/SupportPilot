import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["RAG_MIN_SCORE"] = "0.35"
os.environ["MIN_CONFIDENCE"] = "0.5"
os.environ["RAG_TOP_K"] = "4"
os.environ["LLM_SMALL_MODEL"] = ""
os.environ["LLM_FALLBACK_MODEL"] = ""

import fakeredis.aioredis  # noqa: E402
import app.core.redis as redis_mod  # noqa: E402


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