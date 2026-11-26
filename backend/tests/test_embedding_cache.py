import hashlib
import json
import uuid

from app.core import redis as redis_module
from app.core.config import settings
from app.rag import retrieve as retrieve_module
from app.rag.store import Hit


class CountingEmbedder:
    def __init__(self):
        self.calls = []
        self.vector = [0.1, 0.2, 0.3]

    async def embed(self, texts):
        self.calls.extend(texts)
        return [self.vector for _ in texts]


def install_fake_retrieval(monkeypatch, embedder, hits):
    async def search(*args, **kwargs):
        return hits

    async def revalidate(tenant_id, matching_hits, active_generations):
        return matching_hits

    monkeypatch.setattr(retrieve_module, "get_embedder", lambda: embedder)
    monkeypatch.setattr(retrieve_module.store, "search", search)
    monkeypatch.setattr(retrieve_module, "_revalidate_hits", revalidate)


async def test_identical_query_uses_tenant_scoped_embedding_cache(monkeypatch):
    tenant_id = uuid.uuid4()
    document_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    question = "Where is my invoice?"
    hit = Hit(str(document_id), "guide.txt", 0, "Invoice details.", 0.9, generation_id)
    embedder = CountingEmbedder()
    install_fake_retrieval(monkeypatch, embedder, [hit])
    monkeypatch.setattr(settings, "embed_model", "cache-test-model", raising=False)

    first = await retrieve_module.retrieve(
        tenant_id, question, {document_id: generation_id}
    )
    second = await retrieve_module.retrieve(
        tenant_id, question, {document_id: generation_id}
    )

    assert first == second == [hit]
    assert embedder.calls == [question]
    digest = hashlib.sha256(
        f"cache-test-model\n{question}".encode("utf-8")
    ).hexdigest()
    key = f"emb:{tenant_id}:{digest}"
    assert question not in key
    cached = await redis_module.redis_client.get(key)
    assert json.loads(cached) == embedder.vector


async def test_same_query_from_different_tenants_misses_cache(monkeypatch):
    question = "Where is my invoice?"
    embedder = CountingEmbedder()
    hit = Hit(str(uuid.uuid4()), "guide.txt", 0, "Invoice details.", 0.9)
    install_fake_retrieval(monkeypatch, embedder, [hit])
    monkeypatch.setattr(settings, "embed_model", "cache-test-model", raising=False)
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    await retrieve_module.retrieve(tenant_a, question, {uuid.uuid4(): None})
    await retrieve_module.retrieve(tenant_b, question, {uuid.uuid4(): None})

    assert embedder.calls == [question, question]
    keys = await redis_module.redis_client.keys("emb:*")
    assert any(str(tenant_a) in key for key in keys)
    assert any(str(tenant_b) in key for key in keys)
    assert all(question not in key for key in keys)


async def test_redis_failure_does_not_break_query_retrieval(monkeypatch):
    tenant_id = uuid.uuid4()
    document_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    question = "Find invoice details"
    hit = Hit(str(document_id), "guide.txt", 0, "Invoice details.", 0.9, generation_id)
    embedder = CountingEmbedder()
    install_fake_retrieval(monkeypatch, embedder, [hit])

    class UnavailableRedis:
        async def get(self, key):
            raise ConnectionError("redis unavailable")

        async def set(self, key, value, ex):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(redis_module, "redis_client", UnavailableRedis())
    results = await retrieve_module.retrieve(
        tenant_id, question, {document_id: generation_id}
    )

    assert results == [hit]
    assert embedder.calls == [question]
