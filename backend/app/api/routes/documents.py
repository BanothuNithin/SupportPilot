import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.tenant import get_owned
from app.core.config import settings
from app.db.session import get_db
from app.models import Document, Role, User
from app.rag import store
from app.rag.extract import ALLOWED_EXTENSIONS
from app.rag.ingest import index_document
from app.schemas.document import DocumentOut

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentOut, status_code=202)
async def upload_document(
    file: UploadFile,
    background: BackgroundTasks,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    filename = os.path.basename(file.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .txt, .md and .pdf files are supported")
    data = await file.read(settings.max_upload_bytes + 1)  # read one byte past the limit to detect oversize
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
    if not data.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    doc = Document(tenant_id=user.tenant_id, filename=filename, storage_key="", status="indexing")
    db.add(doc)
    await db.commit()
    background.add_task(index_document, doc.id, user.tenant_id, filename, data)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Document).where(Document.tenant_id == user.tenant_id).order_by(Document.created_at.desc()))
    return list(rows)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    user: User = Depends(require_roles(Role.owner, Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    doc = await get_owned(db, Document, document_id, user.tenant_id)
    await store.delete_document(user.tenant_id, doc.id)
    await db.delete(doc)
    await db.commit()
