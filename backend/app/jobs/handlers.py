import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.actions.executor import ToolExecutionError, get_tool_executor, redact
from app.actions.registry import TOOLS, validate_arguments
from app.actions.sanitize import truncate
from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.service import get_env
from app.jobs.cleanup import LEGACY_GENERATION_ID
from app.models import Document, DocumentIndexGeneration, PendingAction, Ticket
from app.rag import store
from app.rag.chunking import chunk_text
from app.rag.embeddings import get_embedder
from app.rag.extract import IngestError, extract_text
from app.storage import get_storage

log = logging.getLogger(__name__)
EMBED_BATCH = 32


def _cleanup_delay() -> timedelta:
    return timedelta(
        seconds=max(settings.job_lease_seconds, settings.job_requeue_after_seconds, 60)
    )


async def handle_index_document(document_id: str | uuid.UUID, tenant_id: str | uuid.UUID) -> None:
    try:
        document_id = uuid.UUID(str(document_id))
        tenant_id = uuid.UUID(str(tenant_id))
        claim_time = datetime.now(timezone.utc)
        claim_token = uuid.uuid4()
        async with SessionLocal() as db:
            result = await db.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.status == "queued",
                    Document.deleted_at.is_(None),
                )
                .values(
                    status="indexing",
                    attempts=Document.attempts + 1,
                    locked_until=claim_time + timedelta(seconds=settings.job_lease_seconds),
                    claim_token=claim_token,
                    updated_at=claim_time,
                )
                .returning(
                    Document.storage_key,
                    Document.filename,
                    Document.attempts,
                    Document.active_generation,
                )
            )
            claimed = result.first()
            if claimed is None:
                await db.rollback()
                return
            storage_key, filename, attempts, active_generation = claimed
            db.add(
                DocumentIndexGeneration(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    generation_id=claim_token,
                    status="staged",
                    is_legacy=False,
                )
            )
            if active_generation is None:
                legacy = await db.get(
                    DocumentIndexGeneration,
                    (tenant_id, document_id, LEGACY_GENERATION_ID),
                )
                if legacy is None:
                    db.add(
                        DocumentIndexGeneration(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            generation_id=LEGACY_GENERATION_ID,
                            status="active",
                            is_legacy=True,
                        )
                    )
            await db.commit()

        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            _renew_lease(document_id, tenant_id, claim_token, lease_lost)
        )
        try:
            if not storage_key:
                raise IngestError("Original file is not stored")
            data = await get_storage().get(storage_key)
            if lease_lost.is_set():
                return
            text = await asyncio.to_thread(extract_text, filename, data)
            chunks = await asyncio.to_thread(chunk_text, text)
            if not chunks:
                raise IngestError("Document contains no indexable text")
            embedder = get_embedder()
            vectors: list[list[float]] = []
            for offset in range(0, len(chunks), EMBED_BATCH):
                if lease_lost.is_set():
                    return
                vectors.extend(await embedder.embed(chunks[offset : offset + EMBED_BATCH]))
            if lease_lost.is_set():
                return
            await store.upsert_generation(
                tenant_id,
                document_id,
                claim_token,
                filename,
                chunks,
                vectors,
            )
            await _publish_generation(
                document_id,
                tenant_id,
                claim_token,
                len(chunks),
            )
        except (IngestError, FileNotFoundError) as exc:
            message = (
                "Original file is missing"
                if isinstance(exc, FileNotFoundError)
                else (
                    str(exc)
                    if str(exc) in {
                        "Document contains no indexable text",
                        "Original file is not stored",
                    }
                    else "Document could not be read or indexed"
                )
            )
            await _set_failed(document_id, tenant_id, claim_token, message)
        except Exception as exc:
            log.warning(
                "document indexing attempt failed document_id=%s error_type=%s",
                document_id,
                type(exc).__name__,
            )
            if attempts < settings.job_max_attempts:
                await _set_queued(document_id, tenant_id, claim_token)
            else:
                await _set_failed(
                    document_id,
                    tenant_id,
                    claim_token,
                    f"Indexing failed after {attempts} attempts",
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
    except Exception as exc:
        log.warning(
            "document indexing handler failed document_id=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )


async def _renew_lease(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim_token: uuid.UUID,
    lease_lost: asyncio.Event,
) -> None:
    interval = max(0.1, settings.job_lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        now = datetime.now(timezone.utc)
        try:
            async with SessionLocal() as db:
                result = await db.execute(
                    update(Document)
                    .where(
                        Document.id == document_id,
                        Document.tenant_id == tenant_id,
                        Document.status == "indexing",
                        Document.claim_token == claim_token,
                        Document.deleted_at.is_(None),
                        Document.locked_until > now,
                    )
                    .values(
                        locked_until=now + timedelta(seconds=settings.job_lease_seconds),
                        updated_at=now,
                    )
                )
                await db.commit()
        except Exception as exc:
            log.warning(
                "document indexing lease renewal failed document_id=%s error_type=%s",
                document_id,
                type(exc).__name__,
            )
            lease_lost.set()
            return
        if result.rowcount != 1:
            lease_lost.set()
            return


async def _publish_generation(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    generation_id: uuid.UUID,
    chunk_count: int,
) -> bool:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        current = await db.scalar(
            select(Document.active_generation).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == "indexing",
                Document.claim_token == generation_id,
                Document.deleted_at.is_(None),
                Document.locked_until > now,
            )
        )
        result = await db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == "indexing",
                Document.claim_token == generation_id,
                Document.deleted_at.is_(None),
                Document.locked_until > now,
            )
            .values(
                active_generation=generation_id,
                status="ready",
                chunk_count=chunk_count,
                error=None,
                claim_token=None,
                locked_until=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await _abandon_generation(db, tenant_id, document_id, generation_id, now)
            await db.commit()
            return False

        staged = await db.execute(
            update(DocumentIndexGeneration)
            .where(
                DocumentIndexGeneration.tenant_id == tenant_id,
                DocumentIndexGeneration.document_id == document_id,
                DocumentIndexGeneration.generation_id == generation_id,
                DocumentIndexGeneration.status == "staged",
            )
            .values(status="active", cleanup_after=None, updated_at=now)
        )
        if staged.rowcount != 1:
            raise RuntimeError("Index generation is no longer staged")

        previous_generation = current or LEGACY_GENERATION_ID
        retired = await db.execute(
            update(DocumentIndexGeneration)
            .where(
                DocumentIndexGeneration.tenant_id == tenant_id,
                DocumentIndexGeneration.document_id == document_id,
                DocumentIndexGeneration.generation_id == previous_generation,
                DocumentIndexGeneration.status == "active",
            )
            .values(
                status="retired",
                cleanup_after=now + _cleanup_delay(),
                updated_at=now,
            )
        )
        if retired.rowcount != 1:
            raise RuntimeError("Previous active index generation is missing")
        await db.commit()
    return True


async def _abandon_generation(
    db,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    generation_id: uuid.UUID,
    now: datetime,
) -> None:
    await db.execute(
        update(DocumentIndexGeneration)
        .where(
            DocumentIndexGeneration.tenant_id == tenant_id,
            DocumentIndexGeneration.document_id == document_id,
            DocumentIndexGeneration.generation_id == generation_id,
            DocumentIndexGeneration.status.in_(["staged", "abandoned"]),
        )
        .values(
            status="abandoned",
            cleanup_after=now + _cleanup_delay(),
            updated_at=now,
        )
    )


async def _set_queued(
    document_id: uuid.UUID, tenant_id: uuid.UUID, claim_token: uuid.UUID
) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == "indexing",
                Document.claim_token == claim_token,
                Document.deleted_at.is_(None),
                Document.locked_until > now,
            )
            .values(
                status="queued",
                locked_until=None,
                claim_token=None,
                updated_at=now,
            )
        )
        await _abandon_generation(db, tenant_id, document_id, claim_token, now)
        await db.commit()


async def _set_failed(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim_token: uuid.UUID,
    message: str,
) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
                Document.status == "indexing",
                Document.claim_token == claim_token,
                Document.deleted_at.is_(None),
                Document.locked_until > now,
            )
            .values(
                status="failed",
                error=message[:300],
                locked_until=None,
                claim_token=None,
                updated_at=now,
            )
        )
        await _abandon_generation(db, tenant_id, document_id, claim_token, now)
        await db.commit()


async def handle_execute_action(
    action_id: str | uuid.UUID, tenant_id: str | uuid.UUID
) -> None:
    started = time.monotonic()
    outcome = "ignored"
    claimed_tenant_id = None
    execution_started = False
    try:
        action_uuid = uuid.UUID(str(action_id))
        tenant_uuid = uuid.UUID(str(tenant_id))
        claim_time = datetime.now(timezone.utc)
        locked_until = claim_time + timedelta(seconds=settings.job_lease_seconds)
        async with SessionLocal() as db:
            claim = await db.execute(
                update(PendingAction)
                .where(
                    PendingAction.id == action_uuid,
                    PendingAction.tenant_id == tenant_uuid,
                    PendingAction.status == "approved",
                )
                .values(
                    status="executing",
                    locked_until=locked_until,
                    updated_at=claim_time,
                )
                .returning(
                    PendingAction.tenant_id,
                    PendingAction.tool,
                    PendingAction.arguments,
                    PendingAction.ticket_id,
                )
            )
            row = claim.first()
            if row is None:
                await db.rollback()
                return
            row_tenant_id, tool, serialized_arguments, ticket_id = row
            claimed_tenant_id = row_tenant_id
            await db.commit()

        spec = TOOLS.get(tool)
        if spec is None:
            await _fail_action(action_uuid, row_tenant_id, "Tool is not allowed")
            outcome = "failed"
            return

        async with SessionLocal() as db:
            env = await get_env(db, row_tenant_id, spec.integration_kind)
        if env is None:
            await _fail_action(action_uuid, row_tenant_id, "Integration not configured")
            outcome = "failed"
            return

        try:
            arguments = validate_arguments(tool, json.loads(serialized_arguments))
            if tool == "create_github_issue":
                marker = f"\n\n<!-- supportpilot-action:{action_uuid} -->"
                arguments["body"] = truncate(
                    arguments["body"], 6000 - len(marker)
                ) + marker
            executor = get_tool_executor()
            execution_started = True
            result = await executor.call(tool, arguments, env)
        except ToolExecutionError as exc:
            await _fail_action(
                action_uuid, row_tenant_id, redact(str(exc), env)[:500]
            )
            outcome = "failed"
            return
        except Exception:
            await _fail_action(
                action_uuid,
                row_tenant_id,
                "Unexpected error while executing the action",
            )
            outcome = "failed"
            return

        finished_at = datetime.now(timezone.utc)
        async with SessionLocal() as db:
            finalized = await db.execute(
                update(PendingAction)
                .where(
                    PendingAction.id == action_uuid,
                    PendingAction.tenant_id == row_tenant_id,
                    PendingAction.status == "executing",
                )
                .values(
                    status="executed",
                    result=json.dumps(result),
                    error=None,
                    executed_at=finished_at,
                    locked_until=None,
                    updated_at=finished_at,
                )
            )
            if finalized.rowcount != 1:
                await db.rollback()
                return
            if (
                tool == "create_github_issue"
                and ticket_id is not None
            ):
                url = result.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    await db.execute(
                        update(Ticket)
                        .where(
                            Ticket.id == ticket_id,
                            Ticket.tenant_id == row_tenant_id,
                        )
                        .values(external_issue_url=url)
                    )
            await db.commit()
        outcome = "executed"
    except Exception as exc:
        outcome = "failed" if claimed_tenant_id is not None else "error"
        if claimed_tenant_id is not None and not execution_started:
            try:
                await _fail_action(
                    action_uuid,
                    claimed_tenant_id,
                    "Unexpected error while executing the action",
                )
            except Exception:
                log.warning(
                    "action failure state could not be saved action_id=%s",
                    action_id,
                )
        log.warning(
            "action execution handler failed action_id=%s error_type=%s",
            action_id,
            type(exc).__name__,
        )
    finally:
        log.info(
            "job finished job_type=execute_action id=%s duration_ms=%s outcome=%s",
            action_id,
            int((time.monotonic() - started) * 1000),
            outcome,
        )


async def _fail_action(
    action_id: uuid.UUID, tenant_id: uuid.UUID, error: str
) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await db.execute(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.tenant_id == tenant_id,
                PendingAction.status == "executing",
            )
            .values(
                status="failed",
                error=error[:500],
                locked_until=None,
                updated_at=now,
            )
        )
        await db.commit()
