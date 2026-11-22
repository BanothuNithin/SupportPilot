import uuid
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from app.llm import get_llm
from app.llm.base import LLMError
from app.llm.fake import FakeProvider
from app.api.routes.chat import send_message
from app.db.session import SessionLocal
from app.main import app
from app.models import Conversation, User
from app.schemas.message import MessageIn


async def register(client, company, email):
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_conversation(client, headers):
    customer = await client.post(
        "/api/v1/customers", headers=headers, json={"email": "person@example.com"}
    )
    assert customer.status_code == 201
    conversation = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer.json()["id"]},
    )
    assert conversation.status_code == 201
    return conversation.json()["id"]


async def test_chat_streams_and_saves_messages_in_order(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Where is my order?"},
    )

    assert response.status_code == 200
    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert "token" in events, response.text
    assert events[-1] == "done"
    assert set(events[:-1]) == {"token"}

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    messages = detail.json()["messages"]
    assert [message["sender"] for message in messages] == ["customer", "ai"]
    assert messages[0]["content"] == "Where is my order?"
    assert messages[1]["content"] == "This is a fake answer to: Where is my order?"


async def test_chat_saves_partial_answer_when_client_disconnects(client):
    class OneTokenProvider:
        async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
            yield "partial answer "

    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)

    async with SessionLocal() as db:
        conversation = await db.get(Conversation, uuid.UUID(conversation_id))
        user = await db.scalar(select(User).where(User.email == "owner@acme.com"))
        assert conversation is not None
        assert user is not None

        response = await send_message(
            uuid.UUID(conversation_id),
            MessageIn(content="Need help"),
            user=user,
            db=db,
            llm=OneTokenProvider(),
        )

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("more_body"):
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    messages = detail.json()["messages"]
    assert [message["sender"] for message in messages] == ["customer", "ai"]
    assert messages[1]["content"] == "partial answer"


async def test_chat_requires_auth_and_restricts_tenant(client):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    conversation_id = await create_conversation(client, headers_a)

    unauthorized = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Hello"},
    )
    cross_tenant = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers_b,
        json={"content": "Hello"},
    )
    empty_content = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers_a,
        json={"content": ""},
    )

    assert unauthorized.status_code == 401
    assert cross_tenant.status_code == 404
    assert empty_content.status_code == 422


async def test_model_failure_sends_error_without_saving_ai_message(
    client, monkeypatch
):
    class FailingProvider:
        def stream(self, messages: list[dict]) -> AsyncIterator[str]:
            async def fail() -> AsyncIterator[str]:
                if not messages:
                    yield ""
                    return
                raise LLMError("model unavailable")

            return fail()

    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: FailingProvider())
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Need help"},
    )

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert response.status_code == 200
    assert events == ["error"]

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert [message["sender"] for message in detail.json()["messages"]] == ["customer"]
