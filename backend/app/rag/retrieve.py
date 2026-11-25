import logging
import uuid
from collections.abc import Mapping

from app.core.config import settings
from app.rag import store
from app.rag.embeddings import get_embedder

log = logging.getLogger(__name__)


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
        return await store.search(
            tenant_id,
            vector,
            settings.rag_top_k,
            settings.rag_min_score,
            active_generations,
        )
    except Exception:
        log.exception("Retrieval failed; continuing without context")
        return []
