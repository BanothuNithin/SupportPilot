import pytest
from moto import mock_aws

from app.storage import FakeStorage, S3Storage, StorageError


@pytest.mark.asyncio
async def test_s3_storage_crud_and_lazy_bucket():
    with mock_aws():
        storage = S3Storage(
            bucket="supportpilot-test",
            region="us-east-1",
            access_key="test-access",
            secret_key="test-secret",
            endpoint_url=None,
        )
        key = "tenants/test/documents/test/file.txt"
        assert await storage.exists(key) is False
        await storage.put(key, b"hello", "text/plain")
        assert await storage.get(key) == b"hello"
        assert await storage.exists(key) is True
        await storage.delete(key)
        assert await storage.exists(key) is False
        with pytest.raises(FileNotFoundError):
            await storage.get("missing.txt")


@pytest.mark.asyncio
async def test_fake_storage_crud():
    storage = FakeStorage()
    await storage.put("key", b"data", "text/plain")
    assert await storage.get("key") == b"data"
    assert await storage.exists("key") is True
    await storage.delete("key")
    assert await storage.exists("key") is False


@pytest.mark.asyncio
async def test_s3_put_unexpected_exception_is_storage_error():
    storage = S3Storage(
        bucket="supportpilot-test",
        region="us-east-1",
        access_key="test-access",
        secret_key="test-secret",
        endpoint_url=None,
    )
    storage._bucket_ready = True
    storage.client.put_object = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider detail"))
    with pytest.raises(StorageError, match="Object storage operation failed"):
        await storage.put("key", b"data", "text/plain")


@pytest.mark.asyncio
async def test_s3_delete_unexpected_exception_is_storage_error():
    storage = S3Storage(
        bucket="supportpilot-test",
        region="us-east-1",
        access_key="test-access",
        secret_key="test-secret",
        endpoint_url=None,
    )
    storage._bucket_ready = True
    storage.client.delete_object = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider detail"))
    with pytest.raises(StorageError, match="Object storage operation failed"):
        await storage.delete("key")


@pytest.mark.asyncio
async def test_s3_get_closes_response_body():
    storage = S3Storage(
        bucket="supportpilot-test",
        region="us-east-1",
        access_key="test-access",
        secret_key="test-secret",
        endpoint_url=None,
    )
    storage._bucket_ready = True

    class Body:
        closed = False

        def read(self):
            return b"exact"

        def close(self):
            self.closed = True

    body = Body()
    storage.client.get_object = lambda **kwargs: {"Body": body}
    assert await storage.get("key") == b"exact"
    assert body.closed is True
