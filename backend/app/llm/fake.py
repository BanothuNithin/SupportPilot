import asyncio
import json
import re
from typing import AsyncIterator


class FakeProvider:
    """Streams a canned answer word by word. Lets you build and test without any model."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        if "TASK: triage" in system:
            lowered = user.lower()
            wants_human = bool(
                re.search(r"\b(human|real person|representative|live agent|support agent)\b", lowered)
            )
            intent = "chitchat" if re.match(r"^(hi|hello|hey|thanks|thank you)\b", lowered.strip()) else "question"
            return json.dumps(
                {"intent": intent, "priority": "medium", "wants_human": wants_human}
            )
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
