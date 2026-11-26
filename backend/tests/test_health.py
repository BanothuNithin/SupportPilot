import app.core.redis as redis_module
import app.main as main_module
import app.rag.store as store
import app.storage as storage_module


CHECKS = {"postgres", "redis", "qdrant", "storage"}


async def test_readiness_returns_ok_when_all_dependencies_are_available(client):
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {
            "postgres": True,
            "redis": True,
            "qdrant": True,
            "storage": True,
        },
    }


def assert_dependency_failed(response, failed_check, secret):
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert set(body["checks"]) == CHECKS
    assert body["checks"][failed_check] is False
    assert all(
        result is True
        for check, result in body["checks"].items()
        if check != failed_check
    )
    assert secret not in response.text


async def test_readiness_reports_postgres_failure_without_details(client, monkeypatch):
    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            raise RuntimeError("postgres://user:password@private-db")

    monkeypatch.setattr(
        main_module, "SessionLocal", lambda: FailingSession(), raising=False
    )
    response = await client.get("/health/ready")

    assert_dependency_failed(response, "postgres", "private-db")


async def test_readiness_reports_redis_failure_without_details(client, monkeypatch):
    class FailingRedis:
        async def ping(self):
            raise RuntimeError("redis://user:password@private-redis")

    monkeypatch.setattr(redis_module, "redis_client", FailingRedis())
    response = await client.get("/health/ready")

    assert_dependency_failed(response, "redis", "private-redis")


async def test_readiness_reports_qdrant_failure_without_details(client, monkeypatch):
    class FailingQdrant:
        async def get_collections(self):
            raise RuntimeError("http://private-qdrant:6333")

    monkeypatch.setattr(store, "client", FailingQdrant())
    response = await client.get("/health/ready")

    assert_dependency_failed(response, "qdrant", "private-qdrant")


async def test_readiness_reports_storage_failure_without_details(client, monkeypatch):
    class FailingStorage:
        async def ping(self):
            raise RuntimeError("s3-secret-bucket")

    monkeypatch.setattr(storage_module, "_storage", FailingStorage())
    response = await client.get("/health/ready")

    assert_dependency_failed(response, "storage", "s3-secret-bucket")
