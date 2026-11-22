"""Model router: picks the model per task and falls back when the primary one fails.

- stream(): the main model writes customer-facing answers.
- complete(): the small model does short structured tasks (triage, grading).
- If a call fails BEFORE producing output, the fallback model is tried. Once tokens have been
  sent to the customer we cannot silently switch models, so the error is raised instead.
"""
import logging
from contextlib import aclosing
from typing import AsyncIterator

from app.llm.base import LLMError, LLMProvider

log = logging.getLogger(__name__)


class ModelRouter:
    def __init__(self, main: LLMProvider, small: LLMProvider | None = None, fallback: LLMProvider | None = None):
        self.main = main
        self.small = small or main
        self.fallback = fallback

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        started = False
        try:
            async with aclosing(self.main.stream(messages)) as tokens:
                async for token in tokens:
                    started = True
                    yield token
        except LLMError as e:
            if started or self.fallback is None:
                raise
            log.warning("Main model failed before output (%s); using the fallback model", e)
            async with aclosing(self.fallback.stream(messages)) as tokens:
                async for token in tokens:
                    yield token

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        try:
            return await self.small.complete(messages, json_mode=json_mode)
        except LLMError as e:
            if self.fallback is None:
                raise
            log.warning("Small model failed (%s); using the fallback model", e)
            return await self.fallback.complete(messages, json_mode=json_mode)
