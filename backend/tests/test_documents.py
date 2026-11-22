from app.main import app
from app.rag import store
from app.rag.store import Hit
from app.llm import get_llm
from app.llm.fake import FakeProvider


async def register(client, company, email, role=None):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    if role is not None:
        invited = await client.post(
            "/api/v1/auth/users",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
            json={"email": f"{role}@{email.split('@')[-1]}", "password": "password123", "role": role},
        )
        assert invited.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def invite_agent(client, owner_headers, email):
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
    knowledge: dict[str, list[Hit]] = {}

    async def upsert_chunks(tenant_id, document_id, filename, chunks, vectors):
        knowledge.setdefault(str(tenant_id), []).extend(
            [
                Hit(str(document_id), filename, i, text, 0.99)
                for i, text in enumerate(chunks)
            ]
        )

    async def search(tenant_id, vector, limit, min_score):
        return knowledge.get(str(tenant_id), [])[:limit]

    async def delete_document(tenant_id, document_id):
        rows = knowledge.get(str(tenant_id), [])
        knowledge[str(tenant_id)] = [
            hit for hit in rows if hit.document_id != str(document_id)
        ]

    monkeypatch.setattr(store, "upsert_chunks", upsert_chunks)
    monkeypatch.setattr(store, "search", search)
    monkeypatch.setattr(store, "delete_document", delete_document)
    return knowledge


async def test_upload_then_list_shows_ready_document_with_chunks(client, monkeypatch):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")

    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.txt", b"Alpha beta gamma delta. " * 20, "text/plain")},
    )

    assert uploaded.status_code == 202
    listed = await client.get("/api/v1/documents", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "ready"
    assert listed.json()[0]["chunk_count"] >= 1


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


async def test_uploaded_document_returns_citation_event(client, monkeypatch):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("support-guide.txt", b"Our refund code is ACME-42.", "text/plain")},
    )
    assert uploaded.status_code == 202
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "What is the refund code?"},
    )

    assert response.status_code == 200
    assert "event: citations" in response.text
    assert "support-guide.txt" in response.text


async def test_other_company_gets_no_citations(client, monkeypatch):
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
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers_b,
        json={"content": "What is the Acme launch password?"},
    )

    assert response.status_code == 200
    assert "event: citations" not in response.text
    assert "acme-private.txt" not in response.text


async def test_deleting_document_removes_knowledge(client, monkeypatch):
    install_fake_store(monkeypatch)
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    uploaded = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("delete-me.txt", b"The secret product code is PURPLE.", "text/plain")},
    )
    assert uploaded.status_code == 202
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
