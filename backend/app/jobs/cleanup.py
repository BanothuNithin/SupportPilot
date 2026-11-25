import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Document, DocumentIndexGeneration
from app.rag import store
from app.storage import get_storage

log = logging.getLogger(__name__)
LEGACY_GENERATION_ID = store.LEGACY_GENERATION_ID


def _retry_delay() -> timedelta:
    return timedelta(
        seconds=max(settings.job_lease_seconds, settings.job_requeue_after_seconds, 60)
    )


async def _generation_is_active(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
    is_legacy: bool,
) -> bool:
    async with SessionLocal() as db:
        row = await db.execute(
            select(Document.active_generation, Document.deleted_at).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
        )
        document = row.first()
    if document is None or document.deleted_at is not None:
        return False
    if is_legacy:
        return document.active_generation is None
    return document.active_generation == generation_id


async def _delete_generation_record(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
    is_legacy: bool,
    now: datetime,
) -> bool:
    if await _generation_is_active(tenant_id, document_id, generation_id, is_legacy):
        return False
    try:
        await store.delete_generation(tenant_id, document_id, generation_id)
    except Exception as exc:
        log.warning(
            "document generation cleanup failed document_id=%s generation_id=%s error_type=%s",
            document_id,
            generation_id,
            type(exc).__name__,
        )
        passes = 0
    else:
        passes = 1
    async with SessionLocal() as db:
        await db.execute(
            update(DocumentIndexGeneration)
            .where(
                DocumentIndexGeneration.tenant_id == tenant_id,
                DocumentIndexGeneration.document_id == document_id,
                DocumentIndexGeneration.generation_id == generation_id,
                DocumentIndexGeneration.status.in_(["retired", "abandoned"]),
            )
            .values(
                cleanup_attempts=DocumentIndexGeneration.cleanup_attempts + 1,
                cleanup_passes=DocumentIndexGeneration.cleanup_passes + passes,
                cleanup_after=now + _retry_delay(),
                updated_at=now,
            )
        )
        await db.commit()
    return passes == 1


async def cleanup_generations_once(now: datetime | None = None, limit: int = 100) -> int:
    now = now or datetime.now(timezone.utc)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    DocumentIndexGeneration.tenant_id,
                    DocumentIndexGeneration.document_id,
                    DocumentIndexGeneration.generation_id,
                    DocumentIndexGeneration.is_legacy,
                )
                .where(
                    DocumentIndexGeneration.status.in_(["retired", "abandoned"]),
                    DocumentIndexGeneration.cleanup_after <= now,
                )
                .order_by(DocumentIndexGeneration.cleanup_after)
                .limit(limit)
            )
        ).all()
    cleaned = 0
    for row in rows:
        if await _delete_generation_record(
            row.tenant_id,
            row.document_id,
            row.generation_id,
            row.is_legacy,
            now,
        ):
            cleaned += 1
    return cleaned


async def cleanup_deleted_documents_once(
    now: datetime | None = None,
    document_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int = 100,
) -> int:
    now = now or datetime.now(timezone.utc)
    conditions = [Document.deleted_at.is_not(None)]
    if document_id is not None:
        conditions.append(Document.id == document_id)
    if tenant_id is not None:
        conditions.append(Document.tenant_id == tenant_id)
    async with SessionLocal() as db:
        documents = (
            await db.scalars(
                select(Document).where(*conditions).order_by(Document.deleted_at).limit(limit)
            )
        ).all()

    deleted = 0
    for document in documents:
        async with SessionLocal() as db:
            generations = (
                await db.scalars(
                    select(DocumentIndexGeneration).where(
                        DocumentIndexGeneration.tenant_id == document.tenant_id,
                        DocumentIndexGeneration.document_id == document.id,
                    )
                )
            ).all()
            if not any(generation.is_legacy for generation in generations):
                db.add(
                    DocumentIndexGeneration(
                        tenant_id=document.tenant_id,
                        document_id=document.id,
                        generation_id=LEGACY_GENERATION_ID,
                        status="retired",
                        is_legacy=True,
                        cleanup_after=now,
                    )
                )
            for generation in generations:
                if generation.status == "active":
                    generation.status = "retired"
                elif generation.status == "staged":
                    generation.status = "abandoned"
                generation.cleanup_after = now
                generation.updated_at = now
            await db.commit()

        async with SessionLocal() as db:
            generations = (
                await db.execute(
                    select(
                        DocumentIndexGeneration.generation_id,
                        DocumentIndexGeneration.is_legacy,
                    ).where(
                        DocumentIndexGeneration.tenant_id == document.tenant_id,
                        DocumentIndexGeneration.document_id == document.id,
                    )
                )
            ).all()

        all_vectors_cleaned = True
        for generation in generations:
            if not await _delete_generation_record(
                document.tenant_id,
                document.id,
                generation.generation_id,
                generation.is_legacy,
                now,
            ):
                all_vectors_cleaned = False

        object_cleaned = True
        if document.storage_key:
            try:
                await get_storage().delete(document.storage_key)
            except Exception as exc:
                object_cleaned = False
                log.warning(
                    "deleted document object cleanup failed document_id=%s error_type=%s",
                    document.id,
                    type(exc).__name__,
                )

        if all_vectors_cleaned and object_cleaned:
            async with SessionLocal() as db:
                result = await db.execute(
                    delete(Document).where(
                        Document.id == document.id,
                        Document.tenant_id == document.tenant_id,
                        Document.deleted_at.is_not(None),
                    )
                )
                await db.commit()
            if result.rowcount == 1:
                deleted += 1
    return deleted
