import asyncio
import json
from typing import AsyncIterator


class FakeProvider:
    """Streams a canned answer word by word. Lets you build and test without any model."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def complete(
        self,
        messages: list[dict],
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        system = messages[0]["content"] if messages else ""
        if "TASK: grade" in system:
            return json.dumps({"can_answer": True, "confidence": 1.0})
        raise ValueError("Fake provider received an unsupported completion prompt")

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = f"This is a fake answer to: {last_user}"
        for word in text.split(" "):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield word + " "
