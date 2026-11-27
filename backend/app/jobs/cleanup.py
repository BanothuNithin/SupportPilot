import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select, update

from app.core.config import settings
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_tenant, worker_tenant_ids
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
    session_factory=WorkerSessionLocal,
) -> bool:
    async with session_factory() as db:
        await set_tenant(db, tenant_id)
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
    session_factory=WorkerSessionLocal,
) -> bool:
    if await _generation_is_active(
        tenant_id, document_id, generation_id, is_legacy, session_factory
    ):
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
    async with session_factory() as db:
        await set_tenant(db, tenant_id)
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
    async with WorkerSessionLocal() as db:
        tenants = await worker_tenant_ids(db)
    rows = []
    for tenant_id in tenants:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
            rows.extend(
                (
                    await db.execute(
                        select(
                            DocumentIndexGeneration.tenant_id,
                            DocumentIndexGeneration.document_id,
                            DocumentIndexGeneration.generation_id,
                            DocumentIndexGeneration.is_legacy,
                            DocumentIndexGeneration.cleanup_after,
                        )
                        .where(
                            DocumentIndexGeneration.tenant_id == tenant_id,
                            DocumentIndexGeneration.status.in_(["retired", "abandoned"]),
                            DocumentIndexGeneration.cleanup_after <= now,
                        )
                        .order_by(DocumentIndexGeneration.cleanup_after)
                        .limit(limit)
                    )
                ).all()
            )
    rows.sort(key=lambda row: row.cleanup_after)
    rows = rows[:limit]
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
    session_factory=WorkerSessionLocal,
) -> int:
    now = now or datetime.now(timezone.utc)
    conditions = [Document.deleted_at.is_not(None)]
    if document_id is not None:
        conditions.append(Document.id == document_id)
    if tenant_id is not None:
        conditions.append(Document.tenant_id == tenant_id)
    conditions.append(
        or_(Document.cleanup_after.is_(None), Document.cleanup_after <= now)
    )
    if tenant_id is not None:
        tenants = [tenant_id]
    else:
        async with session_factory() as db:
            tenants = await worker_tenant_ids(db)
    documents = []
    for scoped_tenant_id in tenants:
        scoped_conditions = [*conditions, Document.tenant_id == scoped_tenant_id]
        async with session_factory() as db:
            await set_tenant(db, scoped_tenant_id)
            documents.extend(
                (
                    await db.scalars(
                        select(Document)
                        .where(*scoped_conditions)
                        .order_by(func.coalesce(Document.cleanup_after, Document.deleted_at))
                        .limit(limit)
                    )
                ).all()
            )
    documents.sort(
        key=lambda document: document.cleanup_after or document.deleted_at
    )
    documents = documents[:limit]

    deleted = 0
    for document in documents:
        async with session_factory() as db:
            await set_tenant(db, document.tenant_id)
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

        async with session_factory() as db:
            await set_tenant(db, document.tenant_id)
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
                session_factory,
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
            async with session_factory() as db:
                await set_tenant(db, document.tenant_id)
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
        else:
            async with session_factory() as db:
                await set_tenant(db, document.tenant_id)
                await db.execute(
                    update(Document)
                    .where(
                        Document.id == document.id,
                        Document.tenant_id == document.tenant_id,
                        Document.deleted_at.is_not(None),
                    )
                    .values(
                        cleanup_after=now + _retry_delay(),
                        updated_at=now,
                    )
                )
                await db.commit()
    return deleted
