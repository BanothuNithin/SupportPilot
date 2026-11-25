import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from app.agents import nodes
from app.core.config import settings
from app.llm import get_llm
from app.llm.base import LLMError
from app.llm.fake import FakeProvider
from app.api.routes.chat import send_message
from app.api.routes import documents as documents_route
from app.db.session import SessionLocal
from app.main import app
from app.models import Conversation, Document, DocumentIndexGeneration, Message, Ticket, User
from app.rag import store
from app.rag.store import Hit
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
        json={"content": "Hello"},
    )

    assert response.status_code == 200
    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert "token" in events, response.text
    assert events[-1] == "done"
    assert "triage" in events
    assert "step" in events
    assert "escalated" not in events

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    messages = detail.json()["messages"]
    assert [message["sender"] for message in messages] == ["customer", "ai"]
    assert messages[0]["content"] == "Hello"
    assert messages[1]["content"] == "This is a fake answer to: Hello"


async def test_chat_only_sends_latest_six_messages_to_model(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setattr(settings, "history_limit", 6, raising=False)

    async def retrieve(tenant_id, question, active_generations):
        return [Hit("doc", "help.txt", 0, "Address update instructions.", 0.9)]

    monkeypatch.setattr(nodes, "retrieve", retrieve)

    async with SessionLocal() as db:
        conversation = await db.get(Conversation, uuid.UUID(conversation_id))
        assert conversation is not None
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add_all(
            [
                Message(
                    tenant_id=conversation.tenant_id,
                    conversation_id=conversation.id,
                    sender="customer",
                    content=f"stored-{index}",
                    created_at=start + timedelta(seconds=index),
                )
                for index in range(9)
            ]
        )
        await db.commit()

    class RecordingProvider:
        def __init__(self):
            self.stream_messages = []

        async def complete(
            self,
            messages: list[dict],
            json_mode: bool = False,
            max_tokens: int | None = None,
        ) -> str:
            return json.dumps({"can_answer": True, "confidence": 0.9})

        async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
            self.stream_messages = messages
            yield "Answer."

    provider = RecordingProvider()
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: provider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "How do I update my address?"},
    )

    assert response.status_code == 200
    history = provider.stream_messages[1:]
    assert [message["content"] for message in history] == [
        "stored-4",
        "stored-5",
        "stored-6",
        "stored-7",
        "stored-8",
        "How do I update my address?",
    ]


async def test_chat_saves_partial_answer_when_client_disconnects(client):
    class OneTokenProvider:
        async def complete(
            self,
            messages: list[dict],
            json_mode: bool = False,
            max_tokens: int | None = None,
        ) -> str:
            raise AssertionError("Hello fast path must not call complete")

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
            MessageIn(content="Hello"),
            user=user,
            db=db,
            llm=OneTokenProvider(),
        )

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if (
            message["type"] == "http.response.body"
            and message.get("more_body")
            and b"event: token" in message.get("body", b"")
        ):
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
        async def complete(
            self,
            messages: list[dict],
            json_mode: bool = False,
            max_tokens: int | None = None,
        ) -> str:
            raise AssertionError("Hello fast path must not call complete")

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
        json={"content": "Hello"},
    )

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert response.status_code == 200
    assert events[-1] == "error"
    assert "triage" in events
    assert "step" in events

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert [message["sender"] for message in detail.json()["messages"]] == ["customer"]


async def test_human_request_escalates_and_creates_ticket(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Please connect me with a human agent."},
    )

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert response.status_code == 200
    assert "escalated" in events
    assert events[-1] == "done"

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert detail.json()["status"] == "escalated"
    async with SessionLocal() as db:
        ticket = await db.scalar(
            select(Ticket).where(Ticket.conversation_id == uuid.UUID(conversation_id))
        )
    assert ticket is not None
    assert ticket.reason == "customer_requested_human"


async def test_question_without_retrieval_escalates_without_citations(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setitem(app.dependency_overrides, get_llm, FakeProvider)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Where is my order?"},
    )

    events = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert response.status_code == 200
    assert "citations" not in events
    assert "escalated" in events

    detail = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert detail.json()["status"] == "escalated"
    assert detail.json()["messages"][-1]["sender"] == "ai"


async def test_chat_discards_results_for_document_tombstoned_after_snapshot(
    client, monkeypatch
):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    tenant_id = uuid.UUID(
        (await client.get("/api/v1/auth/me", headers=headers)).json()["tenant_id"]
    )
    document_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                filename="private-guide.txt",
                storage_key="",
                content_type="text/plain",
                size_bytes=32,
                status="ready",
                active_generation=generation_id,
            )
        )
        db.add(
            DocumentIndexGeneration(
                tenant_id=tenant_id,
                document_id=document_id,
                generation_id=generation_id,
                status="active",
            )
        )
        await db.commit()

    search_started = asyncio.Event()
    release_search = asyncio.Event()
    captured_snapshots = []
    retrieved_hits = []
    deleted_text = "Deleted document must not enter the answer."

    async def delayed_search(
        search_tenant_id, vector, limit, min_score, active_generations
    ):
        captured_snapshots.append(dict(active_generations))
        search_started.set()
        await release_search.wait()
        hit = Hit(
            str(document_id),
            "private-guide.txt",
            0,
            deleted_text,
            0.99,
            generation_id=generation_id,
        )
        return [hit]

    original_retrieve = nodes.retrieve

    async def record_retrieval(search_tenant_id, question, active_generations):
        hits = await original_retrieve(
            search_tenant_id,
            question,
            active_generations,
        )
        retrieved_hits.append(hits)
        return hits

    async def defer_document_cleanup(*args, **kwargs):
        return 0

    class RecordingProvider:
        def __init__(self):
            self.complete_calls = []
            self.stream_calls = []

        async def complete(self, messages, json_mode=False, max_tokens=None):
            self.complete_calls.append(messages)
            return json.dumps({"can_answer": True, "confidence": 1.0})

        async def stream(self, messages):
            self.stream_calls.append(messages)
            yield "Answer based on retrieved context."

    provider = RecordingProvider()
    monkeypatch.setattr(store, "search", delayed_search)
    monkeypatch.setattr(nodes, "retrieve", record_retrieval)
    monkeypatch.setattr(
        documents_route,
        "cleanup_deleted_documents_once",
        defer_document_cleanup,
    )
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: provider)

    chat_request = asyncio.create_task(
        client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "How do I reset my account password?"},
        )
    )
    await asyncio.wait_for(search_started.wait(), timeout=5)
    assert captured_snapshots == [{document_id: generation_id}]

    deleted = await client.delete(
        f"/api/v1/documents/{document_id}",
        headers=headers,
    )
    assert deleted.status_code == 204
    async with SessionLocal() as db:
        tombstone = await db.get(Document, document_id)
        assert tombstone is not None
        assert tombstone.deleted_at is not None

    release_search.set()
    response = await asyncio.wait_for(chat_request, timeout=10)

    assert response.status_code == 200
    assert retrieved_hits == [[]]
    assert "citations" not in [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert deleted_text not in response.text
    assert provider.complete_calls == []
    assert provider.stream_calls == []
