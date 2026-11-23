import json

import httpx

from app.core.config import settings
from app.llm import get_llm
from app.llm.fake import FakeProvider
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.router import ModelRouter


def test_get_llm_builds_router_with_small_and_fallback_models(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai_compat")
    monkeypatch.setattr(settings, "llm_model", "big")
    monkeypatch.setattr(settings, "llm_small_model", "tiny")
    monkeypatch.setattr(settings, "llm_fallback_model", "backup")
    llm = get_llm()
    assert isinstance(llm, ModelRouter)
    assert (llm.main.model, llm.small.model, llm.fallback.model) == ("big", "tiny", "backup")


def test_get_llm_without_optional_models_reuses_main_and_has_no_fallback(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai_compat")
    monkeypatch.setattr(settings, "llm_model", "big")
    monkeypatch.setattr(settings, "llm_small_model", "")
    monkeypatch.setattr(settings, "llm_fallback_model", "")
    llm = get_llm()
    assert isinstance(llm, ModelRouter) and llm.small is llm.main and llm.fallback is None


def test_get_llm_fake_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "fake")
    assert isinstance(get_llm(), FakeProvider)


async def test_openai_completion_sends_json_mode_and_token_limit():
    request_payload = {}

    async def handler(request):
        request_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"can_answer": true}'}}]},
        )

    provider = OpenAICompatProvider(
        "http://test/v1",
        "test-key",
        "test-model",
        transport=httpx.MockTransport(handler),
    )

    response = await provider.complete(
        [{"role": "system", "content": "grade"}],
        json_mode=True,
        max_tokens=80,
    )

    assert response == '{"can_answer": true}'
    assert request_payload["max_tokens"] == 80
    assert request_payload["response_format"] == {"type": "json_object"}
    assert request_payload["temperature"] == 0


async def test_model_router_passes_completion_token_limit():
    class CapturingProvider:
        def __init__(self):
            self.max_tokens = None

        async def complete(
            self, messages: list[dict], json_mode: bool = False, max_tokens=None
        ) -> str:
            self.max_tokens = max_tokens
            return '{"can_answer": true}'

        async def stream(self, messages: list[dict]):
            yield "answer"

    provider = CapturingProvider()
    router = ModelRouter(main=provider)

    await router.complete([], json_mode=True, max_tokens=80)

    assert provider.max_tokens == 80