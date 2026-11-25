import logging
import uuid
from collections.abc import Mapping

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Document
from app.rag import store
from app.rag.embeddings import get_embedder

log = logging.getLogger(__name__)


async def _revalidate_hits(
    tenant_id: uuid.UUID,
    hits: list[store.Hit],
    active_generations: Mapping[uuid.UUID, uuid.UUID | None],
) -> list[store.Hit]:
    candidates: list[tuple[store.Hit, uuid.UUID]] = []
    document_ids: set[uuid.UUID] = set()
    missing = object()
    for hit in hits:
        try:
            document_id = uuid.UUID(hit.document_id)
        except (AttributeError, TypeError, ValueError):
            continue
        requested_generation = active_generations.get(document_id, missing)
        if requested_generation is missing or requested_generation != hit.generation_id:
            continue
        candidates.append((hit, document_id))
        document_ids.add(document_id)
    if not candidates:
        return []

    async with SessionLocal() as db:
        rows = await db.execute(
            select(Document.id, Document.active_generation).where(
                Document.tenant_id == tenant_id,
                Document.id.in_(document_ids),
                Document.deleted_at.is_(None),
            )
        )
        current_generations = {row.id: row.active_generation for row in rows.all()}
    return [
        hit
        for hit, document_id in candidates
        if current_generations.get(document_id, missing) == hit.generation_id
    ]


async def retrieve(
    tenant_id: uuid.UUID,
    question: str,
    active_generations: Mapping[uuid.UUID, uuid.UUID | None],
) -> list[store.Hit]:
    """Top matching chunks of THIS tenant. If retrieval breaks, chat still works (without knowledge)."""
    try:
        if not active_generations:
            return []
        [vector] = await get_embedder().embed([question])
        hits = await store.search(
            tenant_id,
            vector,
            settings.rag_top_k,
            settings.rag_min_score,
            active_generations,
        )
        return await _revalidate_hits(tenant_id, hits, active_generations)
    except Exception:
        log.exception("Retrieval failed; continuing without context")
        return []
