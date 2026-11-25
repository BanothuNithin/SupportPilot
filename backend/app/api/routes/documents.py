import os
import uuid
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.tenant import get_owned
from app.core.config import settings
from app.db.session import get_db
from app.models import Document, DocumentIndexGeneration, Role, User
from app.rag import store
from app.rag.extract import ALLOWED_EXTENSIONS
from app.jobs.queue import enqueue
from app.jobs.cleanup import cleanup_deleted_documents_once
from app.schemas.document import DocumentOut
from app.storage import get_storage, StorageError

router = APIRouter(prefix="/documents", tags=["documents"])
log = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ord(ch) >= 32)
    name = name.strip()
    return (name or "document")[:200]


def content_type_for(ext: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(ext, "application/octet-stream")


def attachment_header(filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_").replace("/", "_")
    ascii_name = "".join(ch for ch in ascii_name if ch not in "\r\n")
    encoded_name = quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'


@router.post("", response_model=DocumentOut, status_code=202)
async def upload_document(
    file: UploadFile,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    filename = sanitize_filename(file.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .txt, .md and .pdf files are supported")
    data = await file.read(settings.max_upload_bytes + 1)  # read one byte past the limit to detect oversize
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
    if not data.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid PDF file")
    if ext in {".txt", ".md"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Text file must be valid UTF-8")
        if b"\x00" in data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Text file contains a NUL byte")

    document_id = uuid.uuid4()
    key = f"tenants/{user.tenant_id}/documents/{document_id}/{uuid.uuid4().hex}{ext}"
    content_type = content_type_for(ext)
    try:
        await get_storage().put(key, data, content_type)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Object storage unavailable",
        ) from exc
    doc = Document(
        id=document_id,
        tenant_id=user.tenant_id,
        filename=filename,
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
        status="queued",
    )
    try:
        db.add(doc)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            log.warning("database rollback failed after uploaded document failure")
        try:
            await get_storage().delete(key)
        except Exception:
            log.warning("uploaded document cleanup failed")
        raise
    await enqueue("index_document", doc.id, user.tenant_id)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Document)
        .where(Document.tenant_id == user.tenant_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
    )
    return list(rows)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    await get_owned(db, Document, document_id, user.tenant_id)
    doc = await db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.tenant_id == user.tenant_id)
        .with_for_update()
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.deleted_at is None:
        now = datetime.now(timezone.utc)
        generations = (
            await db.scalars(
                select(DocumentIndexGeneration).where(
                    DocumentIndexGeneration.tenant_id == user.tenant_id,
                    DocumentIndexGeneration.document_id == doc.id,
                )
            )
        ).all()
        known_generations = {generation.generation_id for generation in generations}
        if doc.active_generation is None:
            legacy_generation_id = store.LEGACY_GENERATION_ID
            if legacy_generation_id not in known_generations:
                generations.append(
                    DocumentIndexGeneration(
                        tenant_id=user.tenant_id,
                        document_id=doc.id,
                        generation_id=legacy_generation_id,
                        status="active",
                        is_legacy=True,
                    )
                )
        elif doc.active_generation not in known_generations:
            generations.append(
                DocumentIndexGeneration(
                    tenant_id=user.tenant_id,
                    document_id=doc.id,
                    generation_id=doc.active_generation,
                    status="active",
                    is_legacy=False,
                )
            )

        doc.deleted_at = now
        doc.active_generation = None
        doc.claim_token = None
        doc.locked_until = None
        doc.updated_at = now
        for generation in generations:
            if generation.status == "active":
                generation.status = "retired"
            elif generation.status == "staged":
                generation.status = "abandoned"
            generation.cleanup_after = now
            generation.updated_at = now
        await db.commit()
    await cleanup_deleted_documents_once(
        document_id=doc.id,
        tenant_id=user.tenant_id,
    )


@router.post("/{document_id}/reindex", response_model=DocumentOut, status_code=202)
async def reindex_document(
    document_id: uuid.UUID,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    doc = await get_owned(db, Document, document_id, user.tenant_id)
    if doc.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.status not in {"failed", "ready"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Document is already queued or indexing")
    if not doc.storage_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "Original file is not stored")
    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
            Document.status.in_(["failed", "ready"]),
            Document.deleted_at.is_(None),
        )
        .values(
            status="queued",
            attempts=0,
            error=None,
            locked_until=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Document is already queued or indexing")
    await db.commit()
    await db.refresh(doc)
    await enqueue("index_document", doc.id, user.tenant_id)
    return doc


@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await get_owned(db, Document, document_id, user.tenant_id)
    if doc.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if not doc.storage_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document object is unavailable")
    try:
        data = await get_storage().get(doc.storage_key)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document object not found")
    except StorageError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Object storage unavailable")
    media_type = doc.content_type if doc.content_type in {
        "application/pdf",
        "text/plain",
        "text/markdown",
    } else "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": attachment_header(doc.filename),
        },
    )
