import uuid

from app.db.session import SessionLocal
from app.models import Conversation


async def register(client, company, email, password="password123"):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": password},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_customer(client, headers):
    response = await client.post(
        "/api/v1/customers", headers=headers, json={"email": "person@example.com"}
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_other_tenant_cannot_create_conversation_for_customer(client):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    customer_id = await create_customer(client, headers_a)

    response = await client.post(
        "/api/v1/conversations",
        headers=headers_b,
        json={"customer_id": customer_id},
    )
    assert response.status_code == 404


async def test_list_conversations_filters_by_status(client):
    headers = await register(client, "Acme", "owner@acme.com")
    customer_id = await create_customer(client, headers)
    first = await client.post(
        "/api/v1/conversations", headers=headers, json={"customer_id": customer_id}
    )
    second = await client.post(
        "/api/v1/conversations", headers=headers, json={"customer_id": customer_id}
    )

    async with SessionLocal() as db:
        escalated = await db.get(Conversation, uuid.UUID(second.json()["id"]))
        escalated.status = "escalated"
        await db.commit()

    default_status = await client.get("/api/v1/conversations?status=ai_handling", headers=headers)
    escalated_status = await client.get("/api/v1/conversations?status=escalated", headers=headers)
    assert [row["id"] for row in default_status.json()] == [first.json()["id"]]
    assert [row["id"] for row in escalated_status.json()] == [second.json()["id"]]


async def test_other_tenant_cannot_read_conversation(client):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    customer_id = await create_customer(client, headers_a)
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers_a,
        json={"customer_id": customer_id},
    )

    response = await client.get(
        f"/api/v1/conversations/{conversation.json()['id']}", headers=headers_b
    )
    assert response.status_code == 404


async def test_customer_with_conversation_cannot_be_deleted(client):
    headers = await register(client, "Acme", "owner@acme.com")
    customer_id = await create_customer(client, headers)
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer_id},
    )
    assert conversation.status_code == 201

    deleted = await client.delete(f"/api/v1/customers/{customer_id}", headers=headers)
    assert deleted.status_code == 409
