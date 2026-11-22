from app.core.config import settings
from app.llm import get_llm
from app.llm.fake import FakeProvider
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