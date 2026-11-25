import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.db.session import SessionLocal
from app.jobs.cleanup import cleanup_deleted_documents_once, cleanup_generations_once
from app.jobs.queue import enqueue
from app.models import Document, DocumentIndexGeneration


async def find_stuck_documents(
    now: datetime,
) -> tuple[
    list[tuple[uuid.UUID, uuid.UUID]],
    list[tuple[uuid.UUID, uuid.UUID, int, uuid.UUID | None]],
]:
    """Scan documents across tenants for queue recovery and expired indexing leases."""
    queued_before = now - timedelta(seconds=settings.job_requeue_after_seconds)
    async with SessionLocal() as db:
        queued_rows = (
            await db.execute(
                select(Document.id, Document.tenant_id)
                .where(
                    Document.status == "queued",
                    Document.deleted_at.is_(None),
                    Document.updated_at < queued_before,
                )
                .order_by(Document.updated_at)
                .limit(100)
            )
        ).all()
        expired_rows = (
            await db.execute(
                select(
                    Document.id,
                    Document.tenant_id,
                    Document.attempts,
                    Document.claim_token,
                )
                .where(
                    Document.status == "indexing",
                    Document.deleted_at.is_(None),
                    Document.locked_until.is_not(None),
                    Document.locked_until < now,
                )
            )
        ).all()
    return (
        [(row.id, row.tenant_id) for row in queued_rows],
        [(row.id, row.tenant_id, row.attempts, row.claim_token) for row in expired_rows],
    )


async def reconcile_once(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    queued, expired = await find_stuck_documents(now)
    counts = {
        "queued": 0,
        "indexing_requeued": 0,
        "indexing_failed": 0,
        "generations_cleaned": 0,
        "documents_deleted": 0,
    }
    for document_id, tenant_id in queued:
        async with SessionLocal() as db:
            await db.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.status == "queued",
                    Document.deleted_at.is_(None),
                )
                .values(updated_at=now)
            )
            await db.commit()
        if await enqueue("index_document", document_id, tenant_id):
            counts["queued"] += 1

    for document_id, tenant_id, attempts, claim_token in expired:
        async with SessionLocal() as db:
            target_status = "failed" if attempts >= settings.job_max_attempts else "queued"
            values = {
                "status": target_status,
                "locked_until": None,
                "claim_token": None,
                "updated_at": now,
            }
            if target_status == "failed":
                values["error"] = "Indexing was interrupted too many times"
            result = await db.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.status == "indexing",
                    Document.claim_token == claim_token,
                    Document.deleted_at.is_(None),
                    Document.locked_until < now,
                )
                .values(**values)
            )
            if result.rowcount == 1:
                if claim_token is not None:
                    await db.execute(
                        update(DocumentIndexGeneration)
                        .where(
                            DocumentIndexGeneration.tenant_id == tenant_id,
                            DocumentIndexGeneration.document_id == document_id,
                            DocumentIndexGeneration.generation_id == claim_token,
                            DocumentIndexGeneration.status == "staged",
                        )
                        .values(
                            status="abandoned",
                            cleanup_after=now
                            + timedelta(
                                seconds=max(
                                    settings.job_lease_seconds,
                                    settings.job_requeue_after_seconds,
                                    60,
                                )
                            ),
                            updated_at=now,
                        )
                    )
            await db.commit()
            if result.rowcount == 1:
                if target_status == "failed":
                    counts["indexing_failed"] += 1
                else:
                    counts["indexing_requeued"] += 1
                    await enqueue("index_document", document_id, tenant_id)
    counts["generations_cleaned"] = await cleanup_generations_once(now)
    counts["documents_deleted"] = await cleanup_deleted_documents_once(now)
    return counts
