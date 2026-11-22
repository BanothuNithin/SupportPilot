"""Qdrant access. ONE collection for all tenants; every search carries a mandatory tenant filter."""
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.core.config import settings

COLLECTION = "chunks"
# Always reach the client as `store.client` (looked up at call time) so tests can swap it.
client = AsyncQdrantClient(location=settings.qdrant_url, check_compatibility=False)


@dataclass
class Hit:
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float


def _tenant_filter(tenant_id: uuid.UUID, document_id: uuid.UUID | None = None) -> models.Filter:
    must = [models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id)))]
    if document_id:
        must.append(models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id))))
    return models.Filter(must=must)


async def ensure_collection(dim: int) -> None:
    if await client.collection_exists(COLLECTION):
        return
    await client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
    # is_tenant tells Qdrant to co-locate each tenant's vectors, which speeds up filtered search
    await client.create_payload_index(
        COLLECTION, "tenant_id", models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True)
    )


async def upsert_chunks(tenant_id: uuid.UUID, document_id: uuid.UUID, filename: str, chunks: list[str], vectors: list[list[float]]) -> None:
    await ensure_collection(len(vectors[0]))
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={"tenant_id": str(tenant_id), "document_id": str(document_id), "filename": filename, "chunk_index": i, "text": text},
        )
        for i, (text, vec) in enumerate(zip(chunks, vectors))
    ]
    await client.upsert(COLLECTION, points=points)


async def search(tenant_id: uuid.UUID, vector: list[float], limit: int, min_score: float) -> list[Hit]:
    if not await client.collection_exists(COLLECTION):
        return []
    res = await client.query_points(
        COLLECTION, query=vector, query_filter=_tenant_filter(tenant_id), limit=limit, score_threshold=min_score, with_payload=True
    )
    return [
        Hit(p.payload["document_id"], p.payload["filename"], p.payload["chunk_index"], p.payload["text"], p.score)
        for p in res.points
    ]


async def delete_document(tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
    if await client.collection_exists(COLLECTION):
        await client.delete(COLLECTION, points_selector=models.FilterSelector(filter=_tenant_filter(tenant_id, document_id)))
