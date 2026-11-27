import uuid

import pytest

from app.db.session import SessionLocal
from tests.tenant_test_utils import set_test_tenant
from app.models import PendingAction, Ticket


async def register(client, company, email):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


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
    return uuid.UUID(conversation.json()["id"])


async def create_action(db, tenant_id, conversation_id, *, status="pending"):
    ticket = Ticket(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        title="Test ticket",
    )
    db.add(ticket)
    await db.flush()
    action = PendingAction(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        ticket_id=ticket.id,
        tool="create_github_issue",
        arguments='{"title":"Test","body":"Test"}',
        status=status,
    )
    db.add(action)
    await db.flush()
    return ticket.id, action.id


async def create_test_data(client):
    owner_a = await register(client, "Acme", "owner@acme.com")
    owner_b = await register(client, "Globex", "owner@globex.com")
    conversation_a1 = await create_conversation(client, owner_a)
    conversation_a2 = await create_conversation(client, owner_a)
    conversation_b = await create_conversation(client, owner_b)
    tenant_a = uuid.UUID((await client.get("/api/v1/auth/me", headers=owner_a)).json()["tenant_id"])
    tenant_b = uuid.UUID((await client.get("/api/v1/auth/me", headers=owner_b)).json()["tenant_id"])

    async with SessionLocal() as db:
        await set_test_tenant(db, tenant_a)
        ticket_a1, pending_a1 = await create_action(db, tenant_a, conversation_a1)
        _, failed_a1 = await create_action(db, tenant_a, conversation_a1, status="failed")
        _, pending_a2 = await create_action(db, tenant_a, conversation_a2)
        await db.commit()
        await set_test_tenant(db, tenant_b)
        _, pending_b = await create_action(db, tenant_b, conversation_b)
        await db.commit()
    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "conversation_a1": conversation_a1,
        "ticket_a1": ticket_a1,
        "pending_a1": pending_a1,
        "failed_a1": failed_a1,
        "pending_a2": pending_a2,
        "pending_b": pending_b,
    }


@pytest.mark.asyncio
async def test_list_actions_filters_by_conversation(client):
    data = await create_test_data(client)
    response = await client.get(
        f"/api/v1/actions?conversation_id={data['conversation_a1']}",
        headers=data["owner_a"],
    )

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {
        str(data["pending_a1"]),
        str(data["failed_a1"]),
    }
    assert {item["conversation_id"] for item in response.json()} == {
        str(data["conversation_a1"]),
    }


@pytest.mark.asyncio
async def test_list_actions_combines_conversation_status_and_ticket_filters(client):
    data = await create_test_data(client)
    response = await client.get(
        "/api/v1/actions",
        params={
            "conversation_id": str(data["conversation_a1"]),
            "status": "pending",
            "ticket_id": str(data["ticket_a1"]),
        },
        headers=data["owner_a"],
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(data["pending_a1"])]


@pytest.mark.asyncio
async def test_list_actions_for_other_tenant_conversation_is_empty(client):
    data = await create_test_data(client)
    response = await client.get(
        f"/api/v1/actions?conversation_id={data['conversation_a1']}",
        headers=data["owner_b"],
    )

    assert response.status_code == 200
    assert response.json() == []
