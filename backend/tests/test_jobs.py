import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import app.core.redis as redis_mod
import app.jobs.handlers as handlers_mod
import app.jobs.queue as queue_mod
import app.jobs.reconciler as reconciler_mod
import app.worker as worker_mod
from sqlalchemy import select
from app.core.config import settings
from app.db.session import SessionLocal
from app.llm.base import LLMError
from app.models import Document
from app.rag import store
from app.rag.embeddings import FakeEmbedder
from app.storage import get_storage


async def register(client, email="owner@jobs.example.com"):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": "Jobs", "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def upload(client, headers, name="guide.txt", contents=b"Durable jobs index these document words."):
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": (name, contents, "text/plain")},
    )
    assert response.status_code == 202
    return response.json()


async def create_queued_document(tenant_id, filename="direct.txt", contents=b"Index these words."):
    document_id = uuid.uuid4()
    key = f"tenants/{tenant_id}/documents/{document_id}/file.txt"
    await get_storage().put(key, contents, "text/plain")
    async with SessionLocal() as db:
        db.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                filename=filename,
                storage_key=key,
                content_type="text/plain",
                size_bytes=len(contents),
                status="queued",
            )
        )
        await db.commit()
    return document_id


async def get_document(document_id):
    async with SessionLocal() as db:
        return await db.get(Document, document_id)


async def search_document(tenant_id, document_id, query):
    doc = await get_document(document_id)
    if doc is None or doc.deleted_at is not None:
        return []
    vector = (await FakeEmbedder().embed([query]))[0]
    return await store.search(
        tenant_id,
        vector,
        20,
        0.0,
        {document_id: doc.active_generation},
    )


async def test_upload_enqueues_and_only_drain_indexes(client, drain_jobs):
    headers = await register(client)
    uploaded = await upload(client, headers)
    assert uploaded["status"] == "queued"
    assert (await get_document(uuid.UUID(uploaded["id"]))).status == "queued"
    assert await drain_jobs() == 1
    assert (await get_document(uuid.UUID(uploaded["id"]))).status == "ready"


async def test_lost_redis_job_is_reconciled_and_drained(client, fake_redis, drain_jobs, monkeypatch):
    headers = await register(client)
    with monkeypatch.context() as patch:
        class RedisWithoutPublish:
            async def lpush(self, *args):
                raise RuntimeError("redis unavailable")

            def __getattr__(self, name):
                return getattr(fake_redis, name)

        patch.setattr(redis_mod, "redis_client", RedisWithoutPublish())
        uploaded = await upload(client, headers)
    document_id = uuid.UUID(uploaded["id"])
    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        doc.updated_at = datetime.now(timezone.utc) - timedelta(
            seconds=settings.job_requeue_after_seconds + 1
        )
        await db.commit()
    result = await reconciler_mod.reconcile_once()
    assert result["queued"] == 1
    assert await drain_jobs() == 1
    assert (await get_document(document_id)).status == "ready"


async def test_duplicate_delivery_and_forced_rerun_keep_vector_count(client, drain_jobs):
    headers = await register(client)
    uploaded = await upload(client, headers, contents=(b"Long document paragraph. " * 120))
    doc_id = uuid.UUID(uploaded["id"])
    doc = await get_document(doc_id)
    tenant_id = doc.tenant_id
    await queue_mod.enqueue("index_document", doc_id, tenant_id)
    assert await drain_jobs() == 2
    doc = await get_document(doc_id)
    assert doc.status == "ready"
    original_chunk_count = doc.chunk_count
    assert original_chunk_count > 1
    async with SessionLocal() as db:
        doc = await db.get(Document, doc_id)
        doc.status = "queued"
        await db.commit()
    await get_storage().put(doc.storage_key, b"Short replacement.", "text/plain")
    await queue_mod.enqueue("index_document", doc_id, tenant_id)
    assert await drain_jobs() == 1
    rerun = await get_document(doc_id)
    assert rerun.active_generation is not None
    points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(
            tenant_id, doc_id, rerun.active_generation
        ),
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )
    assert rerun.chunk_count == len(points) == 1
    assert rerun.chunk_count < original_chunk_count


async def test_concurrent_claim_runs_pipeline_once(client, drain_jobs, monkeypatch):
    headers = await register(client)
    uploaded = await upload(client, headers)
    doc_id = uuid.UUID(uploaded["id"])
    embed_calls = []
    upsert_calls = []

    class CountingEmbedder:
        async def embed(self, texts):
            embed_calls.append(texts)
            return await FakeEmbedder().embed(texts)

    async def upsert(*args):
        upsert_calls.append(args)

    monkeypatch.setattr("app.jobs.handlers.get_embedder", lambda: CountingEmbedder())
    monkeypatch.setattr(store, "upsert_generation", upsert)
    await asyncio.gather(
        worker_mod.handlers["index_document"](doc_id, (await get_document(doc_id)).tenant_id),
        worker_mod.handlers["index_document"](doc_id, (await get_document(doc_id)).tenant_id),
    )
    assert len(embed_calls) == 1
    assert len(upsert_calls) == 1
    assert (await get_document(doc_id)).attempts == 1


async def test_transient_failure_requeues_and_exhaustion_fails(client, drain_jobs, monkeypatch):
    headers = await register(client)
    uploaded = await upload(client, headers)
    doc_id = uuid.UUID(uploaded["id"])

    class FailingEmbedder:
        async def embed(self, texts):
            raise LLMError("provider details must not be stored")

    monkeypatch.setattr("app.jobs.handlers.get_embedder", lambda: FailingEmbedder())
    handler = worker_mod.handlers["index_document"]
    await handler(doc_id, (await get_document(doc_id)).tenant_id)
    doc = await get_document(doc_id)
    assert doc.status == "queued"
    assert doc.attempts == 1
    monkeypatch.setattr(settings, "job_max_attempts", 2)
    await handler(doc_id, doc.tenant_id)
    doc = await get_document(doc_id)
    assert doc.status == "failed"
    assert doc.error == "Indexing failed after 2 attempts"


async def test_empty_document_fails_immediately(client, fake_storage):
    headers = await register(client)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    doc_id = await create_queued_document(uuid.UUID(me["tenant_id"]), contents=b"   ")
    await worker_mod.handlers["index_document"](doc_id, uuid.UUID(me["tenant_id"]))
    doc = await get_document(doc_id)
    assert doc.status == "failed"
    assert doc.attempts == 1
    assert "no indexable text" in doc.error.lower()


@pytest.mark.parametrize(("attempts", "expected"), [(1, "queued"), (3, "failed")])
async def test_reconciler_recovers_expired_leases(attempts, expected):
    tenant_id = uuid.uuid4()
    doc_id = await create_queued_document(tenant_id)
    async with SessionLocal() as db:
        doc = await db.get(Document, doc_id)
        doc.status = "indexing"
        doc.attempts = attempts
        doc.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        doc.claim_token = uuid.uuid4()
        await db.commit()
    counts = await reconciler_mod.reconcile_once()
    doc = await get_document(doc_id)
    assert doc.status == expected
    assert doc.claim_token is None
    if expected == "failed":
        assert doc.error == "Indexing was interrupted too many times"
        assert counts["indexing_failed"] == 1
    else:
        assert counts["indexing_requeued"] == 1


async def test_handler_wrong_tenant_does_nothing():
    tenant_id = uuid.uuid4()
    doc_id = await create_queued_document(tenant_id)
    await worker_mod.handlers["index_document"](doc_id, uuid.uuid4())
    doc = await get_document(doc_id)
    assert doc.status == "queued"
    assert doc.attempts == 0


async def test_reindex_rules_and_failed_reindex(client, drain_jobs, monkeypatch):
    owner = await register(client, "owner@reindex.example.com")
    created = await upload(client, owner)
    doc_id = created["id"]
    queued = await client.post(f"/api/v1/documents/{doc_id}/reindex", headers=owner)
    assert queued.status_code == 409
    async with SessionLocal() as db:
        doc = await db.get(Document, uuid.UUID(doc_id))
        doc.storage_key = ""
        doc.status = "failed"
        doc.error = "previous failure"
        await db.commit()
    missing = await client.post(f"/api/v1/documents/{doc_id}/reindex", headers=owner)
    assert missing.status_code == 409
    assert missing.json()["detail"] == "Original file is not stored"
    async with SessionLocal() as db:
        doc = await db.get(Document, uuid.UUID(doc_id))
        doc.storage_key = "tenants/reindex/documents/reindex/file.txt"
        doc.status = "failed"
        await db.commit()
    reindexed = await client.post(f"/api/v1/documents/{doc_id}/reindex", headers=owner)
    assert reindexed.status_code == 202
    assert reindexed.json()["status"] == "queued"
    assert reindexed.json()["error"] is None


async def test_agent_cannot_reindex_document(client):
    owner = await register(client, "owner@agent-reindex.example.com")
    invited = await client.post(
        "/api/v1/auth/users",
        headers=owner,
        json={
            "email": "agent@agent-reindex.example.com",
            "password": "password123",
            "role": "agent",
        },
    )
    assert invited.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "agent@agent-reindex.example.com",
            "password": "password123",
        },
    )
    agent = {"Authorization": f"Bearer {login.json()['access_token']}"}
    uploaded = await upload(client, owner)
    response = await client.post(
        f"/api/v1/documents/{uploaded['id']}/reindex",
        headers=agent,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("old_worker_fails", [False, True])
async def test_expired_claim_cannot_finalize_after_second_worker_claims(
    client, fake_redis, monkeypatch, old_worker_fails
):
    headers = await register(client, f"lease-{old_worker_fails}@jobs.example.com")
    uploaded = await upload(client, headers)
    document_id = uuid.UUID(uploaded["id"])
    doc = await get_document(document_id)
    tenant_id = doc.tenant_id
    await queue_mod.dequeue(1)

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    embed_calls = 0

    class PausedEmbedder:
        async def embed(self, texts):
            nonlocal embed_calls
            embed_calls += 1
            if embed_calls == 1:
                first_started.set()
                await release_first.wait()
                if old_worker_fails:
                    raise LLMError("old attempt failed")
            else:
                second_started.set()
                await release_second.wait()
            return await FakeEmbedder().embed(texts)

    async def no_op_upsert(*args):
        return None

    monkeypatch.setattr("app.jobs.handlers.get_embedder", lambda: PausedEmbedder())
    monkeypatch.setattr(store, "upsert_generation", no_op_upsert)
    handler = worker_mod.handlers["index_document"]
    first_worker = asyncio.create_task(handler(document_id, tenant_id))
    await asyncio.wait_for(first_started.wait(), timeout=5)

    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        old_token = doc.claim_token
        assert old_token is not None
        doc.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    await reconciler_mod.reconcile_once()
    await queue_mod.dequeue(1)
    assert (await get_document(document_id)).claim_token is None

    second_worker = asyncio.create_task(handler(document_id, tenant_id))
    await asyncio.wait_for(second_started.wait(), timeout=5)
    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        new_token = doc.claim_token
        assert doc.status == "indexing"
        assert new_token is not None and new_token != old_token
        assert doc.attempts == 2
        second_lease = doc.locked_until

    release_first.set()
    await asyncio.wait_for(first_worker, timeout=5)
    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        assert doc.status == "indexing"
        assert doc.claim_token == new_token
        assert doc.error is None
        assert doc.chunk_count == 0
        assert doc.locked_until == second_lease

    release_second.set()
    await asyncio.wait_for(second_worker, timeout=5)
    doc = await get_document(document_id)
    assert doc.status == "ready"
    assert doc.claim_token is None


async def test_indexing_lease_is_renewed(client, monkeypatch):
    headers = await register(client, "lease-renew@jobs.example.com")
    uploaded = await upload(client, headers)
    document_id = uuid.UUID(uploaded["id"])
    doc = await get_document(document_id)
    await queue_mod.dequeue(1)
    monkeypatch.setattr(settings, "job_lease_seconds", 3)
    embed_started = asyncio.Event()
    release_embed = asyncio.Event()

    class PausedEmbedder:
        async def embed(self, texts):
            embed_started.set()
            await release_embed.wait()
            return await FakeEmbedder().embed(texts)

    async def no_op_upsert(*args):
        return None

    monkeypatch.setattr("app.jobs.handlers.get_embedder", lambda: PausedEmbedder())
    monkeypatch.setattr(store, "upsert_generation", no_op_upsert)
    task = asyncio.create_task(
        worker_mod.handlers["index_document"](document_id, doc.tenant_id)
    )
    await asyncio.wait_for(embed_started.wait(), timeout=5)
    async with SessionLocal() as db:
        claimed = await db.get(Document, document_id)
        first_lease = claimed.locked_until
        assert claimed.claim_token is not None
    await asyncio.sleep(1.2)
    async with SessionLocal() as db:
        renewed = await db.get(Document, document_id)
        renewed_until = renewed.locked_until
    if renewed_until.tzinfo is None:
        renewed_until = renewed_until.replace(tzinfo=timezone.utc)
    if first_lease.tzinfo is None:
        first_lease = first_lease.replace(tzinfo=timezone.utc)
    assert renewed_until > first_lease
    release_embed.set()
    await asyncio.wait_for(task, timeout=5)


async def test_stale_qdrant_write_cannot_replace_published_generation(
    client, monkeypatch
):
    headers = await register(client, "qdrant-generation-race@jobs.example.com")
    uploaded = await upload(client, headers, contents=b"Old generation searchable text.")
    document_id = uuid.UUID(uploaded["id"])
    doc = await get_document(document_id)
    tenant_id = doc.tenant_id
    await queue_mod.dequeue(1)

    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    original_upsert = store.upsert_generation
    write_count = 0
    generations = []

    async def delay_first_write(
        write_tenant_id, write_document_id, generation_id, filename, chunks, vectors
    ):
        nonlocal write_count
        write_count += 1
        generations.append(generation_id)
        if write_count == 1:
            first_write_started.set()
            await release_first_write.wait()
        await original_upsert(
            write_tenant_id,
            write_document_id,
            generation_id,
            filename,
            chunks,
            vectors,
        )

    monkeypatch.setattr(store, "upsert_generation", delay_first_write)
    handler = worker_mod.handlers["index_document"]
    stale_worker = asyncio.create_task(handler(document_id, tenant_id))
    await asyncio.wait_for(first_write_started.wait(), timeout=5)

    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        doc.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    await reconciler_mod.reconcile_once()
    await queue_mod.dequeue(1)
    await get_storage().put(
        doc.storage_key, b"New generation is the only searchable text.", "text/plain"
    )

    await handler(document_id, tenant_id)
    published = await get_document(document_id)
    new_generation = published.active_generation
    assert new_generation is not None
    assert new_generation != generations[0]

    release_first_write.set()
    await asyncio.wait_for(stale_worker, timeout=5)
    active_generations = {document_id: new_generation}
    query = await FakeEmbedder().embed(["New generation searchable text"])
    hits = await store.search(tenant_id, query[0], 10, 0.0, active_generations)

    assert hits
    assert all("New generation is the only searchable text." in hit.text for hit in hits)
    points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(tenant_id, document_id, new_generation),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert points
    assert (await get_document(document_id)).active_generation == new_generation
    await reconciler_mod.cleanup_generations_once(
        datetime.now(timezone.utc)
        + timedelta(
            seconds=max(
                settings.job_lease_seconds,
                settings.job_requeue_after_seconds,
                60,
            )
            + 1
        )
    )
    active_points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(tenant_id, document_id, new_generation),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert active_points
    after_cleanup = await search_document(tenant_id, document_id, "New generation")
    assert [hit.text for hit in after_cleanup] == [hit.text for hit in hits]


async def test_legacy_generation_filter_and_multiple_documents(client):
    headers = await register(client, "generation-filter@jobs.example.com")
    tenant_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["tenant_id"])
    first_id = await create_queued_document(tenant_id, "legacy.txt", b"Legacy searchable phrase.")
    second_id = await create_queued_document(tenant_id, "second.txt", b"Second searchable phrase.")
    second_generation = uuid.uuid4()
    vector = (await FakeEmbedder().embed(["shared query"]))[0]

    async with SessionLocal() as db:
        first = await db.get(Document, first_id)
        first.status = "ready"
        second = await db.get(Document, second_id)
        second.status = "ready"
        second.active_generation = second_generation
        await db.commit()

    await store.ensure_collection(len(vector))
    await store.client.upsert(
        store.COLLECTION,
        points=[
            store.models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "tenant_id": str(tenant_id),
                    "document_id": str(first_id),
                    "filename": "legacy.txt",
                    "chunk_index": 0,
                    "text": "Legacy searchable phrase.",
                },
            )
        ],
        wait=True,
    )
    await store.upsert_generation(
        tenant_id,
        second_id,
        second_generation,
        "second.txt",
        ["Second searchable phrase."],
        [vector],
    )
    inactive_generation = uuid.uuid4()
    await store.upsert_generation(
        tenant_id,
        second_id,
        inactive_generation,
        "second.txt",
        ["Inactive stale phrase."],
        [vector],
    )

    hits = await store.search(
        tenant_id,
        vector,
        10,
        0.0,
        {first_id: None, second_id: second_generation},
    )
    assert {hit.document_id for hit in hits} == {str(first_id), str(second_id)}
    assert not any("Inactive stale" in hit.text for hit in hits)
    replacement_generation = uuid.uuid4()
    await store.upsert_generation(
        tenant_id,
        first_id,
        replacement_generation,
        "legacy.txt",
        ["Replacement generation phrase."],
        [vector],
    )
    async with SessionLocal() as db:
        first = await db.get(Document, first_id)
        first.active_generation = replacement_generation
        await db.commit()
    hits_after_publish = await store.search(
        tenant_id,
        vector,
        10,
        0.0,
        {first_id: replacement_generation, second_id: second_generation},
    )
    assert not any("Legacy searchable phrase." in hit.text for hit in hits_after_publish)
    assert any("Replacement generation phrase." in hit.text for hit in hits_after_publish)


async def test_failed_replacement_preserves_active_generation(client, drain_jobs, monkeypatch):
    headers = await register(client, "failed-replacement@jobs.example.com")
    uploaded = await upload(client, headers, contents=b"Previously searchable version.")
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    original = await get_document(document_id)
    active_generation = original.active_generation
    assert active_generation is not None
    response = await client.post(
        f"/api/v1/documents/{document_id}/reindex",
        headers=headers,
    )
    assert response.status_code == 202
    await queue_mod.dequeue(1)

    async def fail_write(*args, **kwargs):
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(store, "upsert_generation", fail_write)
    await worker_mod.handlers["index_document"](document_id, original.tenant_id)
    current = await get_document(document_id)
    assert current.status == "queued"
    assert current.active_generation == active_generation
    hits = await search_document(
        original.tenant_id,
        document_id,
        "Previously searchable version",
    )
    assert hits
    assert all("Previously searchable version." in hit.text for hit in hits)


async def test_qdrant_write_without_database_publication_keeps_old_generation(
    client, drain_jobs, monkeypatch
):
    headers = await register(client, "publication-failure@jobs.example.com")
    uploaded = await upload(client, headers, contents=b"Old published version.")
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    original = await get_document(document_id)
    active_generation = original.active_generation
    response = await client.post(
        f"/api/v1/documents/{document_id}/reindex",
        headers=headers,
    )
    assert response.status_code == 202
    await queue_mod.dequeue(1)

    async def fail_publication(*args, **kwargs):
        raise RuntimeError("database publication failed")

    monkeypatch.setattr(handlers_mod, "_publish_generation", fail_publication)
    await worker_mod.handlers["index_document"](document_id, original.tenant_id)
    current = await get_document(document_id)
    assert current.status == "queued"
    assert current.active_generation == active_generation
    hits = await search_document(original.tenant_id, document_id, "Old published version")
    assert hits
    assert all("Old published version." in hit.text for hit in hits)


async def test_abandoned_staging_generation_is_retried_by_cleanup(client, monkeypatch):
    headers = await register(client, "staging-cleanup@jobs.example.com")
    uploaded = await upload(client, headers)
    document_id = uuid.UUID(uploaded["id"])
    doc = await get_document(document_id)
    await queue_mod.dequeue(1)
    original_upsert = store.upsert_generation
    write_finished = asyncio.Event()
    hold_worker = asyncio.Event()

    async def write_then_hold(*args, **kwargs):
        await original_upsert(*args, **kwargs)
        write_finished.set()
        await hold_worker.wait()

    monkeypatch.setattr(store, "upsert_generation", write_then_hold)
    task = asyncio.create_task(
        worker_mod.handlers["index_document"](document_id, doc.tenant_id)
    )
    await asyncio.wait_for(write_finished.wait(), timeout=5)
    async with SessionLocal() as db:
        claimed = await db.get(Document, document_id)
        generation_id = claimed.claim_token
        claimed.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    now = datetime.now(timezone.utc)
    await reconciler_mod.reconcile_once(now)
    delay = max(settings.job_lease_seconds, settings.job_requeue_after_seconds, 60)
    await reconciler_mod.cleanup_generations_once(now + timedelta(seconds=delay + 1))
    remaining, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(doc.tenant_id, document_id, generation_id),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert remaining == []


async def test_cleanup_after_publication_never_deletes_active_generation(
    client, drain_jobs, monkeypatch
):
    headers = await register(client, "cleanup-after-publish@jobs.example.com")
    uploaded = await upload(client, headers, contents=b"Before replacement.")
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    first = await get_document(document_id)
    old_generation = first.active_generation
    response = await client.post(
        f"/api/v1/documents/{document_id}/reindex",
        headers=headers,
    )
    assert response.status_code == 202
    await get_storage().put(first.storage_key, b"After replacement.", "text/plain")
    await drain_jobs()
    current = await get_document(document_id)
    assert current.active_generation != old_generation
    delay = max(settings.job_lease_seconds, settings.job_requeue_after_seconds, 60)
    original_delete = store.delete_generation
    fail_old_delete = True

    async def fail_once(tenant_id, cleanup_document_id, generation_id):
        nonlocal fail_old_delete
        if generation_id == old_generation and fail_old_delete:
            fail_old_delete = False
            raise RuntimeError("temporary Qdrant delete failure")
        await original_delete(tenant_id, cleanup_document_id, generation_id)

    monkeypatch.setattr(store, "delete_generation", fail_once)
    cleanup_time = datetime.now(timezone.utc) + timedelta(seconds=delay + 1)
    await reconciler_mod.cleanup_generations_once(
        cleanup_time
    )
    assert (await get_document(document_id)).active_generation == current.active_generation
    monkeypatch.setattr(store, "delete_generation", original_delete)
    await reconciler_mod.cleanup_generations_once(
        cleanup_time + timedelta(seconds=delay + 1)
    )
    active_points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(
            first.tenant_id,
            document_id,
            current.active_generation,
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    old_points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._generation_filter(
            first.tenant_id,
            document_id,
            old_generation,
        ),
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert active_points
    assert old_points == []


async def test_concurrent_reindex_requests_only_queue_once(client, drain_jobs):
    headers = await register(client, "concurrent-reindex@jobs.example.com")
    uploaded = await upload(client, headers)
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    responses = await asyncio.gather(
        client.post(f"/api/v1/documents/{document_id}/reindex", headers=headers),
        client.post(f"/api/v1/documents/{document_id}/reindex", headers=headers),
    )
    assert sorted(response.status_code for response in responses) == [202, 409]


async def test_document_deletion_racing_with_late_generation_write(client, drain_jobs, monkeypatch):
    headers = await register(client, "delete-generation-race@jobs.example.com")
    uploaded = await upload(client, headers, contents=b"Old searchable generation.")
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    doc = await get_document(document_id)
    response = await client.post(
        f"/api/v1/documents/{document_id}/reindex",
        headers=headers,
    )
    assert response.status_code == 202
    await queue_mod.dequeue(1)
    await get_storage().put(doc.storage_key, b"Late deleted generation.", "text/plain")

    write_started = asyncio.Event()
    release_write = asyncio.Event()
    original_upsert = store.upsert_generation

    async def delay_write(*args, **kwargs):
        write_started.set()
        await release_write.wait()
        await original_upsert(*args, **kwargs)

    monkeypatch.setattr(store, "upsert_generation", delay_write)
    task = asyncio.create_task(
        worker_mod.handlers["index_document"](document_id, doc.tenant_id)
    )
    await asyncio.wait_for(write_started.wait(), timeout=5)
    deleted = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    assert deleted.status_code == 204
    release_write.set()
    await asyncio.wait_for(task, timeout=5)
    assert await get_document(document_id) is None

    delay = max(settings.job_lease_seconds, settings.job_requeue_after_seconds, 60)
    await reconciler_mod.cleanup_generations_once(
        datetime.now(timezone.utc) + timedelta(seconds=delay + 1)
    )
    points, _ = await store.client.scroll(
        store.COLLECTION,
        scroll_filter=store._tenant_filter(doc.tenant_id, document_id),
        limit=100,
        with_payload=True,
        with_vectors=False,
    )
    assert points == []
    assert await search_document(doc.tenant_id, document_id, "Late deleted generation") == []


async def test_tenant_cannot_retrieve_or_delete_another_tenant_generation(client, drain_jobs):
    tenant_a_headers = await register(client, "generation-owner-a@jobs.example.com")
    tenant_b_headers = await register(client, "generation-owner-b@jobs.example.com")
    uploaded = await upload(
        client,
        tenant_b_headers,
        contents=b"Tenant B private generation.",
    )
    document_id = uuid.UUID(uploaded["id"])
    await drain_jobs()
    doc = await get_document(document_id)
    generation_id = doc.active_generation
    assert generation_id is not None
    tenant_a_id = uuid.UUID(
        (await client.get("/api/v1/auth/me", headers=tenant_a_headers)).json()["tenant_id"]
    )
    await store.delete_generation(tenant_a_id, document_id, generation_id)
    await worker_mod.handlers["index_document"](document_id, tenant_a_id)
    assert await search_document(doc.tenant_id, document_id, "Tenant B private")
    assert await store.search(
        tenant_a_id,
        (await FakeEmbedder().embed(["Tenant B private"]))[0],
        10,
        0.0,
        {document_id: generation_id},
    ) == []


async def test_worker_stops_after_processed_job_and_drops_bad_jobs(monkeypatch, fake_redis):
    stop = asyncio.Event()
    seen = []

    async def raising(job_id, tenant_id):
        seen.append("raising")
        raise RuntimeError("should be isolated")

    async def finish(job_id, tenant_id):
        seen.append("finish")
        stop.set()

    monkeypatch.setattr(worker_mod, "handlers", {"raise": raising, "finish": finish})
    await queue_mod.enqueue("raise", uuid.uuid4(), uuid.uuid4())
    await queue_mod.enqueue("unknown", uuid.uuid4(), uuid.uuid4())
    await queue_mod.enqueue("finish", uuid.uuid4(), uuid.uuid4())
    await asyncio.wait_for(worker_mod.run_worker(stop=stop, concurrency=2), timeout=10)
    assert seen == ["raising", "finish"]
