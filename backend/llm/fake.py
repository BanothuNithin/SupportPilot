import asyncio
from typing import AsyncIterator


class FakeProvider:
    """Streams a canned answer word by word. Lets you build and test without any model."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = f"This is a fake answer to: {last_user}"
        for word in text.split(" "):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield word + " "
