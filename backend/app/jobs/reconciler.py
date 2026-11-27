import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.db.session import WorkerSessionLocal
from app.db.tenant import set_tenant, worker_tenant_ids
from app.jobs.cleanup import cleanup_deleted_documents_once, cleanup_generations_once
from app.jobs.queue import enqueue
from app.models import Document, DocumentIndexGeneration, PendingAction

ACTION_UNKNOWN_ERROR = (
    "Outcome unknown: the worker stopped while executing this action. "
    "Check the target system (GitHub or Slack) before retrying."
)


async def find_stuck_documents(
    now: datetime,
) -> tuple[
    list[tuple[uuid.UUID, uuid.UUID]],
    list[tuple[uuid.UUID, uuid.UUID, int, uuid.UUID | None]],
]:
    """Scan documents across tenants for queue recovery and expired indexing leases."""
    queued_before = now - timedelta(seconds=settings.job_requeue_after_seconds)
    async with WorkerSessionLocal() as db:
        tenants = await worker_tenant_ids(db)
    queued_rows = []
    expired_rows = []
    for tenant_id in tenants:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
            queued_rows.extend(
                (
                    await db.execute(
                        select(Document.id, Document.tenant_id, Document.updated_at)
                        .where(
                            Document.tenant_id == tenant_id,
                            Document.status == "queued",
                            Document.deleted_at.is_(None),
                            Document.updated_at < queued_before,
                        )
                        .order_by(Document.updated_at)
                        .limit(100)
                    )
                ).all()
            )
            expired_rows.extend(
                (
                    await db.execute(
                        select(
                            Document.id,
                            Document.tenant_id,
                            Document.attempts,
                            Document.claim_token,
                        )
                        .where(
                            Document.tenant_id == tenant_id,
                            Document.status == "indexing",
                            Document.deleted_at.is_(None),
                            Document.locked_until.is_not(None),
                            Document.locked_until < now,
                        )
                    )
                ).all()
            )
    queued_rows.sort(key=lambda row: row.updated_at)
    queued_rows = queued_rows[:100]
    return (
        [(row.id, row.tenant_id) for row in queued_rows],
        [(row.id, row.tenant_id, row.attempts, row.claim_token) for row in expired_rows],
    )


async def reconcile_once(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    queued, expired = await find_stuck_documents(now)
    approved_before = now - timedelta(seconds=settings.job_requeue_after_seconds)
    async with WorkerSessionLocal() as db:
        tenants = await worker_tenant_ids(db)
    stale_actions = []
    expired_actions = []
    for tenant_id in tenants:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
            stale_actions.extend(
                (
                    await db.execute(
                        select(
                            PendingAction.id,
                            PendingAction.tenant_id,
                            PendingAction.updated_at,
                        )
                        .where(
                            PendingAction.tenant_id == tenant_id,
                            PendingAction.status == "approved",
                            PendingAction.updated_at < approved_before,
                        )
                        .order_by(PendingAction.updated_at)
                        .limit(100)
                    )
                ).all()
            )
            expired_actions.extend(
                (
                    await db.execute(
                        select(PendingAction.id, PendingAction.tenant_id)
                        .where(
                            PendingAction.tenant_id == tenant_id,
                            PendingAction.status == "executing",
                            PendingAction.locked_until.is_not(None),
                            PendingAction.locked_until < now,
                        )
                    )
                ).all()
            )
    stale_actions.sort(key=lambda row: row.updated_at)
    stale_actions = stale_actions[:100]
    counts = {
        "queued": 0,
        "indexing_requeued": 0,
        "indexing_failed": 0,
        "actions_queued": 0,
        "actions_failed": 0,
        "generations_cleaned": 0,
        "documents_deleted": 0,
    }
    for document_id, tenant_id in queued:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
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
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
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
    for action_id, tenant_id, _ in stale_actions:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
            result = await db.execute(
                update(PendingAction)
                .where(
                    PendingAction.id == action_id,
                    PendingAction.tenant_id == tenant_id,
                    PendingAction.status == "approved",
                    PendingAction.updated_at < approved_before,
                )
                .values(updated_at=now)
            )
            await db.commit()
        if result.rowcount == 1 and await enqueue(
            "execute_action", action_id, tenant_id
        ):
            counts["actions_queued"] += 1

    for action_id, tenant_id in expired_actions:
        async with WorkerSessionLocal() as db:
            await set_tenant(db, tenant_id)
            result = await db.execute(
                update(PendingAction)
                .where(
                    PendingAction.id == action_id,
                    PendingAction.tenant_id == tenant_id,
                    PendingAction.status == "executing",
                    PendingAction.locked_until.is_not(None),
                    PendingAction.locked_until < now,
                )
                .values(
                    status="failed",
                    error=ACTION_UNKNOWN_ERROR,
                    locked_until=None,
                    updated_at=now,
                )
            )
            await db.commit()
        if result.rowcount == 1:
            counts["actions_failed"] += 1

    counts["generations_cleaned"] = await cleanup_generations_once(now)
    counts["documents_deleted"] = await cleanup_deleted_documents_once(now)
    return counts
