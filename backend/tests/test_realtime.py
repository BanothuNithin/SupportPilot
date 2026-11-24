import asyncio
import json
import uuid

import anyio
import pytest
from sqlalchemy import select

import app.core.redis as redis_mod
from app.db.session import SessionLocal
from app.main import app
from app.models import Conversation, Message, PendingAction, Ticket
from app.realtime import channel, conversation_events, publish_message


async def register(client, company, email, role=None):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    owner = {"Authorization": f"Bearer {response.json()['access_token']}"}
    if role is None:
        return owner
    invited = await client.post(
        "/api/v1/auth/users",
        headers=owner,
        json={"email": f"{role}@{email.split('@')[-1]}", "password": "password123", "role": role},
    )
    assert invited.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": f"{role}@{email.split('@')[-1]}", "password": "password123"},
    )
    return owner, {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_conversation(client, headers):
    customer = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={"email": "person@example.com"},
    )
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer.json()["id"]},
    )
    assert conversation.status_code == 201
    return conversation.json()["id"]


async def escalate(client, headers, conversation_id, text="The app crashes"):
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": text},
    )
    assert response.status_code == 200
    async with SessionLocal() as db:
        conv = await db.get(Conversation, uuid.UUID(conversation_id))
        conv.status = "escalated"
        ticket = await db.scalar(
            select(Ticket).where(Ticket.conversation_id == uuid.UUID(conversation_id)).order_by(Ticket.created_at.desc())
        )
        if ticket is None:
            db.add(Ticket(
                tenant_id=conv.tenant_id,
                conversation_id=conv.id,
                title=f"Escalated: {text[:100]}",
                status="open",
                reason="bug_report",
            ))
        await db.commit()
    return response


@pytest.mark.parametrize("role", ["owner", "admin", "agent"])
async def test_human_reply_roles(client, role):
    if role == "owner":
        headers = await register(client, "Acme", "owner@acme.com")
    else:
        owner, headers = await register(
            client,
            "Acme",
            "owner@acme.com",
            role=role,
        )
    conversation_id = await create_conversation(client, owner if role != "owner" else headers)
    await escalate(client, owner if role != "owner" else headers, conversation_id)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=headers,
        json={"content": "We are investigating."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sender"] == "human"
    assert body["author_id"]

    async with SessionLocal() as db:
        message = await db.get(Message, uuid.UUID(body["id"]))
        ticket = await db.scalar(
            select(Ticket).where(Ticket.conversation_id == uuid.UUID(conversation_id)).order_by(Ticket.created_at.desc())
        )
    assert message.sender == "human"
    assert message.author_id == uuid.UUID(body["author_id"])
    assert ticket.status == "in_progress"
    assert ticket.assigned_to == message.author_id


async def test_human_reply_status_validation_and_tenant(client):
    owner = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, owner)
    assert (await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=owner,
        json={"content": "Reply"},
    )).status_code == 409

    owner_b = await register(client, "Globex", "owner@globex.com")
    assert (await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=owner_b,
        json={"content": "Reply"},
    )).status_code == 404

    await escalate(client, owner, conversation_id)
    assert (await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=owner,
        json={"content": ""},
    )).status_code == 422
    assert (await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=owner,
        json={"content": "x" * 4001},
    )).status_code == 422


async def test_human_reply_publishes_exactly_once(client):
    owner = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, owner)
    await escalate(client, owner, conversation_id)

    pubsub = redis_mod.redis_client.pubsub()
    name = channel(uuid.UUID((await client.get("/api/v1/auth/me", headers=owner)).json()["tenant_id"]), uuid.UUID(conversation_id))
    await pubsub.subscribe(name)
    await pubsub.get_message(timeout=0.01)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/human-reply",
        headers=owner,
        json={"content": "We are investigating."},
    )
    assert response.status_code == 200

    event = await pubsub.get_message(timeout=1)
    assert event is not None
    payload = json.loads(event["data"])
    assert payload["type"] == "message"
    assert payload["message"]["sender"] == "human"
    assert await pubsub.get_message(timeout=0.05) is None
    await pubsub.unsubscribe(name)
    await pubsub.aclose()


async def test_customer_message_to_escalated_conversation_publishes(client):
    owner = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, owner)
    await escalate(client, owner, conversation_id)
    tenant_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=owner)).json()["tenant_id"])

    pubsub = redis_mod.redis_client.pubsub()
    name = channel(tenant_id, uuid.UUID(conversation_id))
    await pubsub.subscribe(name)
    await pubsub.get_message(timeout=0.01)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=owner,
        json={"content": "Any update?"},
    )
    assert response.status_code == 200
    event = await pubsub.get_message(timeout=1)
    assert event is not None
    payload = json.loads(event["data"])
    assert payload["message"]["sender"] == "customer"
    assert payload["message"]["content"] == "Any update?"
    await pubsub.unsubscribe(name)
    await pubsub.aclose()


async def test_events_auth_and_cross_tenant(client):
    owner = await register(client, "Acme", "owner@acme.com")
    owner_b = await register(client, "Globex", "owner@globex.com")
    conversation_id = await create_conversation(client, owner)
    assert (await client.get(
        f"/api/v1/conversations/{conversation_id}/events",
    )).status_code == 401
    assert (await client.get(
        f"/api/v1/conversations/{conversation_id}/events",
        headers=owner_b,
    )).status_code == 404

    me_a = await client.get("/api/v1/auth/me", headers=owner)
    me_b = await client.get("/api/v1/auth/me", headers=owner_b)
    tenant_a = uuid.UUID(me_a.json()["tenant_id"])
    tenant_b = uuid.UUID(me_b.json()["tenant_id"])
    assert channel(tenant_a, uuid.UUID(conversation_id)).startswith("conv:")
    assert str(tenant_a) in channel(tenant_a, uuid.UUID(conversation_id))
    assert str(tenant_b) not in channel(tenant_a, uuid.UUID(conversation_id))


async def test_channel_isolation(fake_redis):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    conversation = uuid.uuid4()
    pubsub_b = redis_mod.redis_client.pubsub()
    await pubsub_b.subscribe(channel(tenant_b, conversation))
    await pubsub_b.get_message(timeout=0.01)
    await publish_message(
        tenant_a,
        conversation,
        {"type": "message", "message": {"sender": "human", "content": "A"}},
    )
    assert await pubsub_b.get_message(timeout=0.05) is None
    await pubsub_b.unsubscribe(channel(tenant_b, conversation))
    await pubsub_b.aclose()


async def test_heartbeat_and_aclose_cleanup():
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    generator = conversation_events(tenant_id, conversation_id, heartbeat_seconds=0.01)
    with anyio.fail_after(1):
        event = await anext(generator)
    assert event == ": ping\n\n"
    await generator.aclose()


async def test_aclose_calls_unsubscribe_and_close(monkeypatch):
    class TrackingPubSub:
        def __init__(self):
            self.subscriptions = set()
            self.closed = False

        async def subscribe(self, name):
            self.subscriptions.add(name)

        async def unsubscribe(self, name):
            self.subscriptions.discard(name)

        async def aclose(self):
            self.closed = True
            self.subscriptions.clear()

        async def get_message(self, **kwargs):
            await asyncio.sleep(0)
            return None

    tracking = TrackingPubSub()
    class TrackingRedis:
        def pubsub(self):
            return tracking

    monkeypatch.setattr(redis_mod, "redis_client", TrackingRedis())
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    generator = conversation_events(tenant_id, conversation_id, heartbeat_seconds=0.01)
    event = await anext(generator)
    assert event == ": ping\n\n"
    await generator.aclose()
    assert tracking.subscriptions == set()
    assert tracking.closed is True


@pytest.mark.parametrize("method", ["human", "chat"])
async def test_broken_redis_does_not_break_requests(client, monkeypatch, method):
    owner = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, owner)
    await escalate(client, owner, conversation_id)

    class BrokenRedis:
        async def publish(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(redis_mod, "redis_client", BrokenRedis())
    if method == "human":
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/human-reply",
            headers=owner,
            json={"content": "We are investigating."},
        )
        assert response.status_code == 200
    else:
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=owner,
            json={"content": "Any update?"},
        )
        assert response.status_code == 200
