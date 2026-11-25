"""Qdrant access. Every query and write is scoped by tenant and document generation."""
import asyncio
import uuid
from dataclasses import dataclass
from typing import Mapping

from qdrant_client import AsyncQdrantClient, models

from app.core.config import settings

COLLECTION = "chunks"
POINT_NAMESPACE = uuid.UUID("d4868589-a65b-4b24-b912-dd86c9ce7a14")
LEGACY_GENERATION_ID = uuid.UUID(int=0)
_collection_lock = asyncio.Lock()
_payload_indexes_ready = False
# Resolve `client` at call time so tests can replace it.
client = AsyncQdrantClient(location=settings.qdrant_url, check_compatibility=False)


@dataclass
class Hit:
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float
    generation_id: uuid.UUID | None = None


def _tenant_filter(tenant_id: uuid.UUID, document_id: uuid.UUID | None = None) -> models.Filter:
    must = [
        models.FieldCondition(
            key="tenant_id",
            match=models.MatchValue(value=str(tenant_id)),
        )
    ]
    if document_id is not None:
        must.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchValue(value=str(document_id)),
            )
        )
    return models.Filter(must=must)


def _generation_filter(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> models.Filter:
    must = [
        models.FieldCondition(
            key="tenant_id",
            match=models.MatchValue(value=str(tenant_id)),
        ),
        models.FieldCondition(
            key="document_id",
            match=models.MatchValue(value=str(document_id)),
        ),
    ]
    if generation_id == LEGACY_GENERATION_ID:
        must.append(
            models.IsEmptyCondition(is_empty=models.PayloadField(key="generation_id"))
        )
    else:
        must.append(
            models.FieldCondition(
                key="generation_id",
                match=models.MatchValue(value=str(generation_id)),
            )
        )
    return models.Filter(must=must)


async def ensure_collection(dim: int) -> None:
    global _payload_indexes_ready
    async with _collection_lock:
        if not await client.collection_exists(COLLECTION):
            await client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(
                    size=dim,
                    distance=models.Distance.COSINE,
                ),
            )
        if _payload_indexes_ready:
            return
        await client.create_payload_index(
            COLLECTION,
            "tenant_id",
            models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD,
                is_tenant=True,
            ),
            wait=True,
        )
        for field in ("document_id", "generation_id"):
            await client.create_payload_index(
                COLLECTION,
                field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        _payload_indexes_ready = True


def _ensure_write_completed(result) -> None:
    status = getattr(result, "status", None)
    if status is not None and getattr(status, "value", status) != "completed":
        raise RuntimeError("Qdrant write did not complete")


async def upsert_generation(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
    filename: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> None:
    if not chunks or len(chunks) != len(vectors):
        raise ValueError("Qdrant chunk and vector counts do not match")
    await ensure_collection(len(vectors[0]))
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(POINT_NAMESPACE, f"{document_id}:{generation_id}:{index}")),
            vector=vector,
            payload={
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "generation_id": str(generation_id),
                "filename": filename,
                "chunk_index": index,
                "text": text,
            },
        )
        for index, (text, vector) in enumerate(zip(chunks, vectors))
    ]
    result = await client.upsert(COLLECTION, points=points, wait=True)
    _ensure_write_completed(result)


async def search(
    tenant_id: uuid.UUID,
    vector: list[float],
    limit: int,
    min_score: float,
    active_generations: Mapping[uuid.UUID, uuid.UUID | None],
) -> list[Hit]:
    if not active_generations or not await client.collection_exists(COLLECTION):
        return []
    should = [
        _generation_filter(
            tenant_id,
            document_id,
            generation_id or LEGACY_GENERATION_ID,
        )
        for document_id, generation_id in active_generations.items()
    ]
    query_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=str(tenant_id)),
            )
        ],
        should=should,
    )
    result = await client.query_points(
        COLLECTION,
        query=vector,
        query_filter=query_filter,
        limit=limit,
        score_threshold=min_score,
        with_payload=True,
    )
    return [
        Hit(
            point.payload["document_id"],
            point.payload["filename"],
            point.payload["chunk_index"],
            point.payload["text"],
            point.score,
            (
                uuid.UUID(str(point.payload["generation_id"]))
                if point.payload.get("generation_id") is not None
                else None
            ),
        )
        for point in result.points
    ]


async def delete_generation(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> None:
    if await client.collection_exists(COLLECTION):
        result = await client.delete(
            COLLECTION,
            points_selector=models.FilterSelector(
                filter=_generation_filter(tenant_id, document_id, generation_id)
            ),
            wait=True,
        )
        _ensure_write_completed(result)


async def delete_document(tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
    if await client.collection_exists(COLLECTION):
        result = await client.delete(
            COLLECTION,
            points_selector=models.FilterSelector(
                filter=_tenant_filter(tenant_id, document_id)
            ),
            wait=True,
        )
        _ensure_write_completed(result)
