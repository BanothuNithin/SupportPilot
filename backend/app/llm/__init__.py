from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.fake import FakeProvider
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.router import ModelRouter


def get_llm() -> LLMProvider:
    if settings.llm_provider != "openai_compat":
        return FakeProvider()
    base, key = settings.llm_base_url, settings.llm_api_key
    main = OpenAICompatProvider(base, key, settings.llm_model)
    small = OpenAICompatProvider(base, key, settings.llm_small_model) if settings.llm_small_model else main
    fallback = None
    if settings.llm_fallback_model:
        fallback = OpenAICompatProvider(
            settings.llm_fallback_base_url or base, settings.llm_fallback_api_key or key, settings.llm_fallback_model
        )
    return ModelRouter(main, small, fallback)