import uuid
import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.main import app
from app.rag import store
from app.rag.store import Hit
from app.llm import get_llm
from app.llm.fake import FakeProvider
from sqlalchemy import select
from app.db.session import SessionLocal as _SessionLocal
from app.models import Document
import app.storage as storage_mod
from app.storage import StorageError
from app.api.routes import documents as documents_route
from tests.tenant_test_utils import activate_test_tenant, tenant_scoped_session_factory

SessionLocal = tenant_scoped_session_factory(_SessionLocal)


async def register(client, company, email, role=None):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    activate_test_tenant(headers)
    if role is not None:
        invited = await client.post(
            "/api/v1/auth/users",
            headers=headers,
            json={"email": f"{role}@{email.split('@')[-1]}", "password": "password123", "role": role},
        )
        assert invited.status_code == 201
    return headers


async def invite_agent(client, owner_headers, email):
    activate_test_tenant(owner_headers)
    response = await client.post(
        "/api/v1/auth/users",
        headers=owner_headers,
        json={"email": email, "password": "password123", "role": "agent"},
    )
    assert response.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_conversation(client, headers):
    activate_test_tenant(headers)
    customer = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={"email": "person@example.com"},
    )
    assert customer.status_code == 201
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer.json()["id"]},
    )
    assert conversation.status_code == 201
    return conversation.json()["id"]


def install_fake_store(monkeypatch):
    knowledge: dict[str, list[tuple[uuid.UUID, Hit]]] = {}

    async def upsert_generation(tenant_id, document_id, generation_id, filename, chunks, vectors):
        knowledge.setdefault(str(tenant_id), []).extend(
            [
                (
                    generation_id,
                    Hit(
                        str(document_id),
                        filename,
                        i,
                        text,
                        0.99,
                        generation_id=generation_id,
                    ),
                )
                for i, text in enumerate(chunks)
            ]
        )

    async def search(tenant_id, vector, limit, min_score, active_generations):
        return [
            hit
            for generation_id, hit in knowledge.get(str(tenant_id), [])
            if active_generations.get(uuid.UUID(hit.document_id)) == generation_id
        ][:limit]

    async def delete_generation(tenant_id, document_id, generation_id):
        rows = knowledge.get(str(tenant_id), [])
        knowledge[str(tenant_id)] = [
            (row_generation, hit)
            for row_generation, hit in rows
            if hit.document_id != str(document_id) or row_generation != generation_id
        ]

    async def delete_document(tenant_id, document_id):
        rows = knowledge.get(str(tenant_id), [])
        knowledge[str(tenant_id)] = [
            (generation_id, hit)
            for generation_id, hit in rows
            if hit.document_id != str(document_id)
        ]

    monkeypatch.setattr(store, "upsert_generation", upsert_generation)
    monkeypatch.setattr(store, "search", search)
    monkeypatch.setattr(store, "delete_generation", delete_generation)
    monkeypatch.setattr(store, "delete_document", delete_document)
    return knowledge


async def test_upload_then_list_shows_ready_document_with_chunks(client, monkeypatch, drain_jobs):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")
    contents = b"Alpha beta gamma delta. " * 20

    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.txt", contents, "text/plain")},
    )

    assert uploaded.status_code == 202
    assert uploaded.json()["status"] == "queued"
    assert uploaded.json()["size_bytes"] == len(contents)
    await drain_jobs()
    listed = await client.get("/api/v1/documents", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "ready"
    assert listed.json()[0]["chunk_count"] >= 1
    assert listed.json()[0]["size_bytes"] == len(contents)


async def test_executable_upload_returns_415(client):
    headers = await register(client, "Acme", "owner@acme.com")

    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 415


async def test_empty_upload_returns_400(client):
    headers = await register(client, "Acme", "owner@acme.com")

    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 400


async def test_agent_cannot_upload_document(client):
    owner_headers = await register(client, "Acme", "owner@acme.com")
    agent_headers = await invite_agent(client, owner_headers, "agent@acme.com")

    response = await client.post(
        "/api/v1/documents",
        headers=agent_headers,
        files={"file": ("guide.txt", b"Company policy", "text/plain")},
    )

    assert response.status_code == 403


async def test_uploaded_document_returns_citation_event(client, monkeypatch, drain_jobs):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("support-guide.txt", b"Our refund code is ACME-42.", "text/plain")},
    )
    assert uploaded.status_code == 202
    await drain_jobs()
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "What is the refund code?"},
    )

    assert response.status_code == 200
    assert "event: citations" in response.text
    assert "support-guide.txt" in response.text


async def test_other_company_gets_no_citations(client, monkeypatch, drain_jobs):
    install_fake_store(monkeypatch)
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    conversation_id = await create_conversation(client, headers_b)
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers_a,
        files={"file": ("acme-private.txt", b"The Acme launch password is orbit.", "text/plain")},
    )
    assert uploaded.status_code == 202
    await drain_jobs()
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers_b,
        json={"content": "What is the Acme launch password?"},
    )

    assert response.status_code == 200
    assert "event: citations" not in response.text
    assert "acme-private.txt" not in response.text


async def test_deleting_document_removes_knowledge(client, monkeypatch, drain_jobs):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("delete-me.txt", b"The secret product code is PURPLE.", "text/plain")},
    )
    assert uploaded.status_code == 202
    await drain_jobs()
    document_id = (await client.get("/api/v1/documents", headers=headers)).json()[0]["id"]
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    deleted = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    assert deleted.status_code == 204

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "What is the secret product code?"},
    )
    assert "event: citations" not in response.text
    assert "delete-me.txt" not in response.text


async def test_company_b_cannot_delete_company_a_document(client, monkeypatch):
    install_fake_store(monkeypatch)
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers_a,
        files={"file": ("acme-only.txt", b"Acme confidential content.", "text/plain")},
    )
    assert uploaded.status_code == 202
    document_id = (await client.get("/api/v1/documents", headers=headers_a)).json()[0]["id"]

    deleted = await client.delete(f"/api/v1/documents/{document_id}", headers=headers_b)

    assert deleted.status_code == 404


async def test_upload_stores_tenant_key_without_original_filename(client, fake_storage):
    headers = await register(client, "Acme", "owner@acme.com")
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("private-guide.txt", b"Hello from the document.", "text/plain")},
    )
    assert uploaded.status_code == 202
    async with SessionLocal() as db:
        doc = await db.scalar(select(Document))
    assert doc.storage_key.startswith(f"tenants/{doc.tenant_id}/documents/{doc.id}/")
    assert "private-guide" not in doc.storage_key
    assert await fake_storage.get(doc.storage_key) == b"Hello from the document."


async def test_pdf_with_text_content_returns_400(client):
    headers = await register(client, "Acme", "owner@acme.com")
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.pdf", b"This is text, not a PDF", "application/pdf")},
    )
    assert response.status_code == 400


async def test_binary_text_file_returns_400(client):
    headers = await register(client, "Acme", "owner@acme.com")
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.txt", b"\xff\xfe\xfd", "text/plain")},
    )
    assert response.status_code == 400


async def test_storage_failure_returns_503_without_document_row(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")

    async def fail_put(*args, **kwargs):
        raise StorageError("storage unavailable")

    monkeypatch.setattr(storage_mod.get_storage(), "put", fail_put)
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.txt", b"Hello", "text/plain")},
    )
    assert response.status_code == 503
    async with SessionLocal() as db:
        assert await db.scalar(select(Document)) is None


async def test_download_exact_bytes_header_and_safe_filename(client, fake_storage):
    headers = await register(client, "Acme", "owner@acme.com")
    hostile = 'quo"te\r\nX-Evil: yes\u20ac.txt'
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": (hostile, b"Exact download bytes", "text/plain")},
    )
    assert uploaded.status_code == 202
    async with SessionLocal() as db:
        doc = await db.scalar(select(Document))
    response = await client.get(f"/api/v1/documents/{doc.id}/download", headers=headers)
    assert response.status_code == 200
    assert response.content == b"Exact download bytes"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert "attachment;" in disposition
    assert "filename*=UTF-8''" in disposition


async def test_download_and_delete_tenant_isolation(client, monkeypatch):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    await client.post(
        "/api/v1/documents",
        headers=headers_a,
        files={"file": ("private.txt", b"Private", "text/plain")},
    )
    activate_test_tenant(headers_a)
    async with SessionLocal() as db:
        doc = await db.scalar(select(Document))

    assert (await client.get(f"/api/v1/documents/{doc.id}/download", headers=headers_b)).status_code == 404
    assert (await client.delete(f"/api/v1/documents/{doc.id}", headers=headers_b)).status_code == 404


async def test_delete_removes_vectors_and_object(client, fake_storage, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("delete.txt", b"Delete me", "text/plain")},
    )
    async with SessionLocal() as db:
        doc = await db.scalar(select(Document))
    removed = []

    async def fake_delete_vectors(tenant_id, document_id, generation_id):
        removed.append((tenant_id, document_id, generation_id))

    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake_storage)
    monkeypatch.setattr(store, "delete_generation", fake_delete_vectors)
    response = await client.delete(f"/api/v1/documents/{doc.id}", headers=headers)
    assert response.status_code == 204
    assert len(removed) == 1
    assert removed[0][:2] == (doc.tenant_id, doc.id)
    assert await fake_storage.exists(doc.storage_key) is False


async def test_empty_storage_key_download_returns_404(client):
    headers = await register(client, "Acme", "owner@acme.com")
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    tenant_id = uuid.UUID(me["tenant_id"])
    async with SessionLocal() as db:
        doc = Document(
            tenant_id=tenant_id,
            filename="legacy.txt",
            storage_key="",
            content_type="text/plain",
            size_bytes=0,
            status="ready",
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
    response = await client.get(f"/api/v1/documents/{doc.id}/download", headers=headers)
    assert response.status_code == 404


async def test_missing_object_download_returns_404(client):
    headers = await register(client, "Acme", "owner@acme.com")
    tenant_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["tenant_id"])
    async with SessionLocal() as db:
        doc = Document(
            tenant_id=tenant_id,
            filename="missing.txt",
            storage_key="tenants/missing/documents/missing/missing.txt",
            content_type="text/plain",
            size_bytes=0,
            status="ready",
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
    response = await client.get(f"/api/v1/documents/{doc.id}/download", headers=headers)
    assert response.status_code == 404


async def test_delete_storage_failure_does_not_block_row_deletion(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("keep-row.txt", b"Data", "text/plain")},
    )
    async with SessionLocal() as db:
        doc = await db.scalar(select(Document))

    storage = storage_mod.get_storage()
    original_delete = storage.delete

    async def fail_delete(key):
        raise StorageError("storage unavailable")

    monkeypatch.setattr(storage, "delete", fail_delete)
    response = await client.delete(f"/api/v1/documents/{doc.id}", headers=headers)
    assert response.status_code == 204
    async with SessionLocal() as db:
        pending = await db.get(Document, doc.id)
        assert pending is not None
        assert pending.deleted_at is not None
        assert pending.cleanup_after is not None
        retry_at = pending.cleanup_after
    monkeypatch.setattr(storage, "delete", original_delete)
    from app.jobs.cleanup import cleanup_deleted_documents_once

    assert (
        await cleanup_deleted_documents_once(
            now=retry_at + timedelta(seconds=1),
            document_id=doc.id,
            tenant_id=doc.tenant_id,
        )
        == 1
    )
    async with SessionLocal() as db:
        assert await db.get(Document, doc.id) is None


@pytest.mark.parametrize("failure_stage", ["insert", "commit"])
async def test_database_failure_compensates_uploaded_object(failure_stage, fake_storage, monkeypatch):
    db_error = RuntimeError(f"{failure_stage} database failure")

    class FailingDb:
        def add(self, doc):
            if failure_stage == "insert":
                raise db_error

        async def commit(self):
            if failure_stage == "commit":
                raise db_error

        async def rollback(self):
            return None

    upload = UploadFile(filename="guide.txt", file=io.BytesIO(b"Guide text"))
    with pytest.raises(RuntimeError) as exc:
        await documents_route.upload_document(
            upload,
            user=SimpleNamespace(tenant_id=uuid.uuid4()),
            db=FailingDb(),
        )
    assert exc.value is db_error
    assert fake_storage.objects == {}


async def test_cleanup_failure_preserves_database_exception(fake_storage, monkeypatch):
    db_error = RuntimeError("original database failure")

    class FailingDb:
        def add(self, doc):
            return None

        async def commit(self):
            raise db_error

        async def rollback(self):
            return None

    async def fail_cleanup(key):
        raise StorageError("cleanup provider failure")

    monkeypatch.setattr(fake_storage, "delete", fail_cleanup)
    upload = UploadFile(filename="guide.txt", file=io.BytesIO(b"Guide text"))
    with pytest.raises(RuntimeError) as exc:
        await documents_route.upload_document(
            upload,
            user=SimpleNamespace(tenant_id=uuid.uuid4()),
            db=FailingDb(),
        )
    assert exc.value is db_error
