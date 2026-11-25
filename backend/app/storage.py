import asyncio
from typing import Protocol

import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def ping(self) -> bool: ...


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def ping(self) -> bool:
        return True


class S3Storage:
    def __init__(
        self,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket or settings.s3_bucket
        self._region_name = region or settings.s3_region
        self._lock = asyncio.Lock()
        self._bucket_ready = False
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url if endpoint_url is not None else (settings.s3_endpoint or None),
            aws_access_key_id=access_key or settings.s3_access_key,
            aws_secret_access_key=secret_key or settings.s3_secret_key,
            region_name=region or settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2},
            ),
        )

    def _is_missing(self, exc: Exception) -> bool:
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            return code in {"404", "NoSuchKey", "NotFound"} or status == 404
        return False

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._lock:
            if self._bucket_ready:
                return
            try:
                await anyio.to_thread.run_sync(lambda: self.client.head_bucket(Bucket=self.bucket))
            except Exception as exc:
                if not self._is_missing(exc):
                    raise StorageError("Object storage operation failed") from exc
                try:
                    await anyio.to_thread.run_sync(
                        lambda: (
                            self.client.create_bucket(
                                Bucket=self.bucket,
                                CreateBucketConfiguration={"LocationConstraint": self._region_name},
                            )
                            if self._region_name != "us-east-1"
                            else self.client.create_bucket(Bucket=self.bucket)
                        )
                    )
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code", ""))
                    if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise StorageError("Object storage operation failed") from exc
                except Exception as exc:
                        raise StorageError("Object storage operation failed") from exc
            self._bucket_ready = True

    @property
    def _region(self) -> str:
        return getattr(self, "_region_name", None) or settings.s3_region

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await self._ensure_bucket()
            await anyio.to_thread.run_sync(
                lambda: self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
            )
        except StorageError:
            raise
        except Exception:
            raise StorageError("Object storage operation failed")

    async def get(self, key: str) -> bytes:
        try:
            await self._ensure_bucket()
            response = await anyio.to_thread.run_sync(
                lambda: self.client.get_object(Bucket=self.bucket, Key=key)
            )
            def read_and_close() -> bytes:
                body = response["Body"]
                try:
                    return body.read()
                finally:
                    body.close()

            return await anyio.to_thread.run_sync(read_and_close)
        except Exception as exc:
            if self._is_missing(exc):
                raise FileNotFoundError(key)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("Object storage operation failed") from exc

    async def delete(self, key: str) -> None:
        try:
            await self._ensure_bucket()
            await anyio.to_thread.run_sync(
                lambda: self.client.delete_object(Bucket=self.bucket, Key=key)
            )
        except StorageError:
            raise
        except Exception:
            raise StorageError("Object storage operation failed")

    async def exists(self, key: str) -> bool:
        try:
            await self._ensure_bucket()
            await anyio.to_thread.run_sync(
                lambda: self.client.head_object(Bucket=self.bucket, Key=key)
            )
            return True
        except Exception as exc:
            if self._is_missing(exc):
                return False
            raise StorageError("Object storage operation failed") from exc

    async def ping(self) -> bool:
        try:
            await self._ensure_bucket()
            return True
        except (StorageError, BotoCoreError, ClientError):
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage()
    return _storage