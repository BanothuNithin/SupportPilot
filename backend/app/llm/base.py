from typing import AsyncIterator, Protocol


class LLMError(Exception):
    """Raised when the model provider fails."""


class LLMProvider(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield the answer as text chunks. `messages` use roles system/user/assistant."""
        ...
