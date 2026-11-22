import logging
import uuid

from app.core.config import settings
from app.rag import store
from app.rag.embeddings import get_embedder

log = logging.getLogger(__name__)


async def retrieve(tenant_id: uuid.UUID, question: str) -> list[store.Hit]:
    """Top matching chunks of THIS tenant. If retrieval breaks, chat still works (without knowledge)."""
    try:
        [vector] = await get_embedder().embed([question])
        return await store.search(tenant_id, vector, settings.rag_top_k, settings.rag_min_score)
    except Exception:
        log.exception("Retrieval failed; continuing without context")
        return []
