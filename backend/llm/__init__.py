from app.core.config import settings
from .base import LLMProvider
from .fake import FakeProvider
from .openai_compat import OpenAICompatProvider


def get_llm() -> LLMProvider:
    if settings.llm_provider == "openai_compat":
        return OpenAICompatProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    return FakeProvider()
