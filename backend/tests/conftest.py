import os

TEST_DB = os.environ.get("TEST_DB", "sqlite").lower()
if TEST_DB == "postgres":
    required_test_urls = (
        "TEST_DATABASE_URL",
        "TEST_MIGRATION_DATABASE_URL",
        "TEST_WORKER_DATABASE_URL",
    )
    missing_test_urls = [name for name in required_test_urls if not os.environ.get(name)]
    if missing_test_urls:
        raise RuntimeError(
            "PostgreSQL tests require " + ", ".join(missing_test_urls)
        )
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["MIGRATION_DATABASE_URL"] = os.environ["TEST_MIGRATION_DATABASE_URL"]
    os.environ["WORKER_DATABASE_URL"] = os.environ["TEST_WORKER_DATABASE_URL"]
    os.environ.setdefault("APP_DB_PASSWORD", "supportpilot-test-password")
    os.environ.setdefault("APP_WORKER_DB_PASSWORD", "supportpilot-test-worker-password")
else:
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
    os.environ["MIGRATION_DATABASE_URL"] = ""
    os.environ["WORKER_DATABASE_URL"] = ""
    os.environ["APP_DB_PASSWORD"] = "supportpilot-test-password"
    os.environ["APP_WORKER_DB_PASSWORD"] = "supportpilot-test-worker-password"

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

import asyncio  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def postgres_schema():
    if TEST_DB != "postgres":
        yield
        return

    owner_url = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"])
    database_name = owner_url.database
    maintenance_url = owner_url.set(database="postgres")

    async def ensure_database():
        admin_engine = create_async_engine(
            maintenance_url,
            poolclass=NullPool,
        )
        try:
            async with admin_engine.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                exists = await connection.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": database_name},
                )
                if exists is None:
                    escaped_name = database_name.replace('"', '""')
                    await connection.exec_driver_sql(
                        f'CREATE DATABASE "{escaped_name}"'
                    )
        finally:
            await admin_engine.dispose()

    asyncio.run(ensure_database())
    alembic_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    yield


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(postgres_schema):
    if TEST_DB == "postgres":
        owner_engine = create_async_engine(
            os.environ["TEST_MIGRATION_DATABASE_URL"],
            poolclass=NullPool,
        )
        try:
            table_names = [table.name for table in Base.metadata.sorted_tables]
            quoted_tables = ", ".join(
                '"' + name.replace('"', '""') + '"' for name in table_names
            )
            async with owner_engine.begin() as conn:
                await conn.execute(
                    text(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE")
                )
        finally:
            await owner_engine.dispose()
    else:
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


def pytest_collection_modifyitems(config, items):
    if TEST_DB == "postgres":
        return
    skip_postgres = pytest.mark.skip(reason="requires TEST_DB=postgres")
    for item in items:
        if item.get_closest_marker("postgres"):
            item.add_marker(skip_postgres)