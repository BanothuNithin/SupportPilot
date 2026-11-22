import logging
import uuid

from app.db.session import SessionLocal
from app.models import Document
from app.rag import store
from app.rag.chunking import chunk_text
from app.rag.embeddings import get_embedder
from app.rag.extract import IngestError, extract_text

log = logging.getLogger(__name__)
EMBED_BATCH = 32


async def _finish(doc_id: uuid.UUID, status: str, chunk_count: int = 0, error: str | None = None) -> None:
    async with SessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc:
            doc.status, doc.chunk_count, doc.error = status, chunk_count, error
            await db.commit()


async def index_document(doc_id: uuid.UUID, tenant_id: uuid.UUID, filename: str, data: bytes) -> None:
    """Background job: extract text, chunk, embed, store vectors, update the document status.
    Runs after the HTTP response, so it opens its own DB session. (Week 6 moves this to a real worker queue.)"""
    try:
        text = extract_text(filename, data)
        chunks = chunk_text(text)
        if not chunks:
            raise IngestError("The document has no text.")
        embedder = get_embedder()
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            vectors.extend(await embedder.embed(chunks[i : i + EMBED_BATCH]))
        await store.upsert_chunks(tenant_id, doc_id, filename, chunks, vectors)
        await _finish(doc_id, "ready", chunk_count=len(chunks))
    except Exception as e:
        log.exception("Indexing failed for document %s", doc_id)
        await _finish(doc_id, "failed", error=str(e)[:300])
