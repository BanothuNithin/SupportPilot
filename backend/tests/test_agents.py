import asyncio
import json
import uuid
from contextlib import aclosing
from typing import AsyncIterator

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents import nodes
from app.agents.nodes import decide, resolve_node
from app.agents.state import Grade, State, Triage
from app.core.config import settings
from app.llm import get_llm
from app.llm.base import LLMError
from app.llm.router import ModelRouter
from app.main import app
from app.rag import store
from app.rag.store import Hit


class ScriptedFakeLLM:
    def __init__(
        self,
        triage: dict | None = None,
        grade: dict | None = None,
        tokens: tuple[str, ...] = ("answer ",),
    ):
        self.triage = triage or {
            "intent": "question",
            "priority": "medium",
            "wants_human": False,
        }
        self.grade = grade or {
            "can_answer": True,
            "confidence": 1.0,
        }
        self.tokens = tokens
        self.complete_tasks: list[str] = []
        self.stream_calls = 0
        self.stream_system_prompts: list[str] = []

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        prompt = messages[0]["content"]
        if "TASK: triage" in prompt:
            self.complete_tasks.append("triage")
            return json.dumps(self.triage)
        if "TASK: grade" in prompt:
            self.complete_tasks.append("grade")
            return json.dumps(self.grade)
        raise AssertionError(f"Unexpected completion prompt: {prompt}")

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        self.stream_calls += 1
        self.stream_system_prompts.append(messages[0]["content"])
        for token in self.tokens:
            yield token


class ProviderStub:
    def __init__(
        self,
        tokens: tuple[str, ...] = (),
        stream_error_after: int | None = None,
        completion: str = "completion",
        complete_error: bool = False,
    ):
        self.tokens = tokens
        self.stream_error_after = stream_error_after
        self.completion = completion
        self.complete_error = complete_error
        self.stream_calls = 0
        self.complete_calls = 0
        self.stream_closed = False

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        self.stream_calls += 1
        try:
            if self.stream_error_after == 0:
                raise LLMError("stream failed")
            for index, token in enumerate(self.tokens, start=1):
                yield token
                if self.stream_error_after == index:
                    raise LLMError("stream failed")
        finally:
            self.stream_closed = True

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        self.complete_calls += 1
        if self.complete_error:
            raise LLMError("completion failed")
        return self.completion


def parse_events(response_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = ""
    for line in response_text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line.removeprefix("data: "))))
    return events


async def register(client, company: str, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"company_name": company, "email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_conversation(client, headers: dict[str, str]) -> str:
    customer = await client.post(
        "/api/v1/customers",
        headers=headers,
        json={"email": "customer@example.com"},
    )
    assert customer.status_code == 201
    response = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": customer.json()["id"]},
    )
    assert response.status_code == 201
    return response.json()["id"]


def use_empty_retrieval(monkeypatch):
    async def empty_retrieve(tenant_id, question):
        return []

    monkeypatch.setattr(nodes, "retrieve", empty_retrieve)


@pytest.mark.parametrize(
    ("triage", "grade", "expected"),
    [
        (
            Triage(intent="question", wants_human=True),
            Grade(can_answer=True, confidence=1.0),
            ("escalate", "customer_requested_human"),
        ),
        (
            Triage(intent="chitchat"),
            Grade(can_answer=False),
            ("answer", "chitchat"),
        ),
        (
            Triage(intent="question"),
            Grade(can_answer=False),
            ("escalate", "no_reliable_answer"),
        ),
        (
            Triage(intent="question"),
            Grade(can_answer=True, confidence=0.1),
            ("escalate", "low_confidence"),
        ),
        (
            Triage(intent="question"),
            Grade(can_answer=True, confidence=0.9),
            ("answer", "grounded"),
        ),
    ],
)
def test_decide_routes_five_triage_and_grade_cases(monkeypatch, triage, grade, expected):
    monkeypatch.setattr(settings, "min_confidence", 0.5)
    assert decide(triage, grade) == expected


async def test_grade_ignores_extra_json_keys():
    class ChattyGrader:
        async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
            return json.dumps(
                {
                    "can_answer": True,
                    "confidence": 0.9,
                    "used_sources": [999],
                    "explanation": "The context directly answers the question.",
                }
            )

    hit = Hit("doc", "policy.txt", 0, "The policy allows a refund.", 0.88)
    result = await nodes.grade_node(
        {"question": "Can I get a refund?", "hits": [hit]},
        {"configurable": {"llm": ChattyGrader()}},
    )

    assert result["grade"].can_answer is True
    assert result["grade"].confidence == pytest.approx(0.9)


async def test_near_miss_escalates_without_citations_or_model_stream(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    hit = Hit(
        document_id=str(uuid.uuid4()),
        filename="returns.txt",
        chunk_index=0,
        text="Returns are accepted within 30 days.",
        score=0.99,
    )

    async def retrieve_near_miss(tenant_id, question):
        return [hit]

    monkeypatch.setattr(nodes, "retrieve", retrieve_near_miss)
    llm = ScriptedFakeLLM(
        grade={"can_answer": False, "confidence": 0.99}
    )
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: llm)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Can I return an item after 90 days?"},
    )

    events = parse_events(response.text)
    names = [name for name, _ in events]
    assert response.status_code == 200
    assert "citations" not in names
    assert "escalated" in names
    assert llm.complete_tasks == ["triage", "grade"]
    assert llm.stream_calls == 0

    tickets = await client.get("/api/v1/tickets", headers=headers)
    assert tickets.status_code == 200
    assert len(tickets.json()) == 1
    assert tickets.json()[0]["reason"] == "no_reliable_answer"
    conversation = await client.get(
        f"/api/v1/conversations/{conversation_id}", headers=headers
    )
    assert conversation.json()["status"] == "escalated"


async def test_grounded_answer_receives_real_rule_when_grader_selects_decoy(
    client, monkeypatch
):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    monkeypatch.setattr(settings, "embed_provider", "fake")
    monkeypatch.setattr(settings, "rag_top_k", 4)

    indexed: dict[str, list[Hit]] = {}

    async def index_chunks(tenant_id, document_id, filename, chunks, vectors):
        indexed.setdefault(str(tenant_id), []).extend(
            [
                Hit(str(document_id), filename, index, text, 0.99 - index * 0.01)
                for index, text in enumerate(chunks)
            ]
        )

    async def retrieve_chunks(tenant_id, vector, limit, min_score):
        return indexed.get(str(tenant_id), [])[:limit]

    monkeypatch.setattr(store, "upsert_chunks", index_chunks)
    monkeypatch.setattr(store, "search", retrieve_chunks)

    question = (
        "How old can a utility bill be to qualify as a deemed Officially Valid "
        "Document (OVD) for proof of address, and how long does the customer have "
        "to submit an OVD with their current address afterward?"
    )
    decoy_text = (
        "A utility bill can qualify as a deemed Officially Valid Document (OVD) "
        "for proof of address. The customer should submit an OVD with their "
        "current address after using the utility bill. "
    ) * 8
    rule_text = (
        "A utility bill is deemed an Officially Valid Document (OVD) for proof of "
        "address when it is not more than two months old. The customer must submit "
        "an OVD with their current address within three months after submitting "
        "the deemed OVD. "
    ) + (
        "This guidance applies to the utility bill, proof of address, the deemed "
        "Officially Valid Document, and the customer's current address. "
    ) * 6
    uploads = [
        ("decoy.txt", decoy_text),
        ("real-rule.txt", rule_text),
    ]
    for filename, text in uploads:
        uploaded = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": (filename, text.encode(), "text/plain")},
        )
        assert uploaded.status_code == 202

    llm = ScriptedFakeLLM(
        grade={"can_answer": True, "used_sources": [1], "confidence": 1.0},
        tokens=("I found the rule.",),
    )
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: llm)
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": question},
    )

    assert response.status_code == 200
    assert llm.stream_system_prompts
    assert "two months" in llm.stream_system_prompts[0]
    assert "three months" in llm.stream_system_prompts[0]


async def test_human_request_keyword_overrides_false_triage(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    use_empty_retrieval(monkeypatch)
    llm = ScriptedFakeLLM(
        triage={"intent": "question", "priority": "medium", "wants_human": False}
    )
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: llm)

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Please connect me with a human agent."},
    )

    events = parse_events(response.text)
    escalation = next(data for name, data in events if name == "escalated")
    assert response.status_code == 200
    assert llm.triage["wants_human"] is False
    assert escalation["reason"] == "customer_requested_human"
    assert llm.stream_calls == 0


async def test_escalated_conversation_waits_until_ticket_is_resolved(client, monkeypatch):
    headers = await register(client, "Acme", "owner@acme.com")
    conversation_id = await create_conversation(client, headers)
    use_empty_retrieval(monkeypatch)
    llm = ScriptedFakeLLM(
        triage={"intent": "chitchat", "priority": "medium", "wants_human": False},
        tokens=("AI reply",),
    )
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: llm)

    first = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "I want a human agent."},
    )
    assert "escalated" in [name for name, _ in parse_events(first.text)]
    tickets = await client.get("/api/v1/tickets", headers=headers)
    ticket_id = tickets.json()[0]["id"]
    assert llm.stream_calls == 0

    waiting = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Any update?"},
    )
    waiting_events = parse_events(waiting.text)
    assert [name for name, _ in waiting_events] == ["escalated", "done"]
    assert waiting_events[0][1]["waiting"] is True
    assert llm.stream_calls == 0

    resolved = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers=headers,
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    resumed = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "Hello"},
    )
    resumed_events = [name for name, _ in parse_events(resumed.text)]
    assert "token" in resumed_events
    assert llm.stream_calls == 1


async def test_tickets_are_tenant_scoped(client, monkeypatch):
    headers_a = await register(client, "Acme", "owner@acme.com")
    headers_b = await register(client, "Globex", "owner@globex.com")
    conversation_id = await create_conversation(client, headers_a)
    use_empty_retrieval(monkeypatch)
    llm = ScriptedFakeLLM()
    monkeypatch.setitem(app.dependency_overrides, get_llm, lambda: llm)

    created = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers_a,
        json={"content": "Please speak to a human."},
    )
    assert "escalated" in [name for name, _ in parse_events(created.text)]
    tickets_a = await client.get("/api/v1/tickets", headers=headers_a)
    tickets_b = await client.get("/api/v1/tickets", headers=headers_b)
    ticket_id = tickets_a.json()[0]["id"]
    cross_tenant_update = await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers=headers_b,
        json={"status": "resolved"},
    )

    assert tickets_a.status_code == 200
    assert len(tickets_a.json()) == 1
    assert tickets_b.status_code == 200
    assert tickets_b.json() == []
    assert cross_tenant_update.status_code == 404


async def test_model_router_uses_fallback_before_first_stream_token():
    main = ProviderStub(stream_error_after=0)
    fallback = ProviderStub(tokens=("fallback one", "fallback two"))
    router = ModelRouter(main=main, fallback=fallback)

    tokens = [token async for token in router.stream([])]

    assert tokens == ["fallback one", "fallback two"]
    assert main.stream_calls == 1
    assert fallback.stream_calls == 1


async def test_model_router_does_not_fallback_after_partial_stream():
    main = ProviderStub(tokens=("main partial",), stream_error_after=1)
    fallback = ProviderStub(tokens=("fallback",))
    router = ModelRouter(main=main, fallback=fallback)
    seen = []

    with pytest.raises(LLMError):
        async for token in router.stream([]):
            seen.append(token)

    assert seen == ["main partial"]
    assert fallback.stream_calls == 0


async def test_model_router_stream_without_fallback_raises():
    main = ProviderStub(stream_error_after=0)
    router = ModelRouter(main=main)

    with pytest.raises(LLMError):
        _ = [token async for token in router.stream([])]

    assert main.stream_calls == 1


async def test_model_router_complete_falls_back_or_propagates():
    small = ProviderStub(complete_error=True)
    fallback = ProviderStub(completion="fallback completion")
    router = ModelRouter(main=ProviderStub(), small=small, fallback=fallback)

    assert await router.complete([], json_mode=True) == "fallback completion"
    assert small.complete_calls == 1
    assert fallback.complete_calls == 1

    no_fallback = ModelRouter(main=ProviderStub(), small=ProviderStub(complete_error=True))
    with pytest.raises(LLMError):
        await no_fallback.complete([])


async def test_model_router_streams_main_and_completes_with_small_or_main():
    main = ProviderStub(tokens=("main answer",), completion="main completion")
    small = ProviderStub(tokens=("small answer",), completion="small completion")
    router = ModelRouter(main=main, small=small)

    assert [token async for token in router.stream([])] == ["main answer"]
    assert await router.complete([]) == "small completion"
    assert main.stream_calls == 1
    assert main.complete_calls == 0
    assert small.complete_calls == 1

    main_only = ProviderStub(completion="main handles completion")
    assert await ModelRouter(main=main_only).complete([]) == "main handles completion"
    assert main_only.complete_calls == 1


async def test_model_router_closes_main_stream_when_consumer_stops_early():
    main = ProviderStub(tokens=("first", "second"))
    router = ModelRouter(main=main)

    async with aclosing(router.stream([])) as tokens:
        async for token in tokens:
            assert token == "first"
            break

    assert main.stream_closed is True


async def test_resolve_stops_emitting_after_cancel_and_closes_provider_stream():
    cancel = asyncio.Event()

    class CancelingProvider:
        closed = False

        async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
            raise AssertionError("resolve does not call complete")

        async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
            try:
                yield "first"
                cancel.set()
                yield "after cancel"
            finally:
                self.closed = True

    provider = CancelingProvider()
    graph = StateGraph(State)
    graph.add_node("resolve", resolve_node)
    graph.add_edge(START, "resolve")
    graph.add_edge("resolve", END)
    compiled = graph.compile()
    initial: State = {
        "question": "Hello",
        "history": [{"role": "user", "content": "Hello"}],
        "tenant_id": uuid.uuid4(),
        "hits": [],
        "grade": Grade(can_answer=True),
        "reason": "grounded",
    }
    emitted = []
    result = {}

    async for mode, chunk in compiled.astream(
        initial,
        config={"configurable": {"llm": provider, "cancel": cancel}},
        stream_mode=["updates", "custom"],
    ):
        if mode == "custom" and chunk["type"] == "token":
            emitted.append(chunk["text"])
        elif mode == "updates":
            result.update(chunk.get("resolve", {}))

    assert emitted == ["first"]
    assert result["answer"] == "first"
    assert provider.closed is True
