import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core import redis as redis_module
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.db.session import SessionLocal
from app.models import Message


async def register(client, email):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": "Rate Limit Tenant", "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def invite_agent(client, owner):
    invited = await client.post(
        "/api/v1/auth/users",
        headers=owner,
        json={"email": "agent@rate-limit.example", "password": "password123", "role": "agent"},
    )
    assert invited.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "agent@rate-limit.example", "password": "password123"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_conversation(client, headers, email):
    customer = await client.post(
        "/api/v1/customers", headers=headers, json={"email": email}
    )
    assert customer.status_code == 201
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer.json()["id"]},
    )
    assert conversation.status_code == 201
    return conversation.json()["id"]


async def user_info(client, headers):
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()


async def send_message(client, conversation_id, headers, text):
    return await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": text},
    )


async def test_user_limit_isolated_with_retry_after_and_no_rejected_message(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "chat_rate_limit_user_per_minute", 20, raising=False)
    monkeypatch.setattr(settings, "chat_rate_limit_tenant_per_minute", 120, raising=False)
    owner = await register(client, "owner@rate-limit.example")
    agent = await invite_agent(client, owner)
    owner_info = await user_info(client, owner)
    agent_info = await user_info(client, agent)
    owner_conversation = await create_conversation(
        client, owner, "owner-customer@rate-limit.example"
    )
    agent_conversation = await create_conversation(
        client, agent, "agent-customer@rate-limit.example"
    )

    user_key = f"rl:chat:user:{owner_info['id']}"
    for _ in range(20):
        await rate_limit(user_key, limit=20, window_seconds=60)

    rejected_text = "private message that must not be stored"
    rejected = await send_message(client, owner_conversation, owner, rejected_text)
    assert rejected.status_code == 429
    assert int(rejected.headers["Retry-After"]) >= 1

    accepted = await send_message(
        client, agent_conversation, agent, "A different user is unaffected"
    )
    assert accepted.status_code == 200
    keys = await redis_module.redis_client.keys("rl:chat:*")
    assert user_key in keys
    assert f"rl:chat:user:{agent_info['id']}" in keys
    assert f"rl:chat:tenant:{owner_info['tenant_id']}" in keys

    async with SessionLocal() as db:
        stored = await db.scalar(
            select(Message.id).where(
                Message.tenant_id == uuid.UUID(owner_info["tenant_id"]),
                Message.content == rejected_text,
            )
        )
    assert stored is None


async def test_tenant_limit_is_shared_across_users(client, monkeypatch):
    monkeypatch.setattr(settings, "chat_rate_limit_user_per_minute", 20, raising=False)
    monkeypatch.setattr(settings, "chat_rate_limit_tenant_per_minute", 1, raising=False)
    owner = await register(client, "owner@rate-limit.example")
    agent = await invite_agent(client, owner)
    owner_info = await user_info(client, owner)
    conversation = await create_conversation(
        client, owner, "owner-customer@rate-limit.example"
    )
    agent_conversation = await create_conversation(
        client, agent, "agent-customer@rate-limit.example"
    )

    first = await send_message(client, conversation, owner, "First message")
    assert first.status_code == 200
    rejected_text = "second user's message"
    second = await send_message(client, agent_conversation, agent, rejected_text)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert await redis_module.redis_client.get(
        f"rl:chat:tenant:{owner_info['tenant_id']}"
    ) == "2"
    async with SessionLocal() as db:
        stored = await db.scalar(
            select(Message.id).where(Message.content == rejected_text)
        )
    assert stored is None


async def test_chat_rate_limiter_fails_open_when_redis_is_unavailable(
    client, monkeypatch, caplog
):
    owner = await register(client, "owner@rate-limit.example")
    user = await user_info(client, owner)
    conversation = await create_conversation(
        client, owner, "customer@rate-limit.example"
    )

    class UnavailableRedis:
        def __init__(self):
            self.keys = []

        def pipeline(self, transaction=True):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def watch(self, key):
            self.keys.append(key)
            raise ConnectionError("redis credentials and host must not be logged")

    unavailable = UnavailableRedis()
    monkeypatch.setattr(redis_module, "redis_client", unavailable)
    content = "message text is not part of limiter logs"
    response = await send_message(client, conversation, owner, content)

    assert response.status_code == 200
    assert unavailable.keys == [
        f"rl:chat:user:{user['id']}",
        f"rl:chat:tenant:{user['tenant_id']}",
    ]
    assert content not in caplog.text
    assert "redis credentials" not in caplog.text


@pytest.mark.parametrize(("ttl", "expected"), [(8, "8"), (0, "1"), (-1, "1")])
async def test_rate_limit_retry_after_uses_remaining_ttl(monkeypatch, ttl, expected):
    class RedisWithTtl:
        def pipeline(self, transaction=True):
            return RedisPipeline()

        async def ttl(self, key):
            return ttl

    class RedisPipeline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def watch(self, key):
            self.key = key

        async def get(self, key):
            return "1"

        def multi(self):
            return None

        def incr(self, key):
            self.key = key

        async def execute(self):
            return [2]

        async def reset(self):
            return None

    monkeypatch.setattr(redis_module, "redis_client", RedisWithTtl())
    with pytest.raises(HTTPException) as exc:
        await rate_limit("rl:test", limit=1, window_seconds=60)

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == expected


async def test_concurrent_initial_rate_limit_calls_keep_counter_and_expiry():
    key = "rl:concurrent-init"
    await asyncio.gather(
        *(rate_limit(key, limit=10, window_seconds=60) for _ in range(10))
    )

    assert await redis_module.redis_client.get(key) == "10"
    ttl = await redis_module.redis_client.ttl(key)
    assert 1 <= ttl <= 60


async def test_initial_expiry_setup_failure_respects_fail_open_mode(monkeypatch):
    class ExpirySetupFailure:
        def __init__(self):
            self.expiry_attempts = 0

        def pipeline(self, transaction=True):
            assert transaction is True
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def watch(self, key):
            assert key == "rl:expiry-failure"

        async def get(self, key):
            return None

        def multi(self):
            return None

        def set(self, key, value, ex):
            assert key == "rl:expiry-failure"
            assert value == 1
            assert ex == 60

        async def execute(self):
            self.expiry_attempts += 1
            raise ConnectionError("expiry setup failed")

    redis = ExpirySetupFailure()
    monkeypatch.setattr(redis_module, "redis_client", redis)

    with pytest.raises(ConnectionError, match="expiry setup failed"):
        await rate_limit("rl:expiry-failure", limit=1, window_seconds=60)

    await rate_limit(
        "rl:expiry-failure",
        limit=1,
        window_seconds=60,
        fail_open=True,
    )
    assert redis.expiry_attempts == 2
