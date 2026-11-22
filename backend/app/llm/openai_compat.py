import json
from typing import AsyncIterator

import httpx

from .base import LLMError


class OpenAICompatProvider:
    """Works with any server that speaks the OpenAI chat API: Ollama (/v1), OpenAI, vLLM, LM Studio..."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, object] = {"model": self.model, "messages": messages, "stream": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        timeout = httpx.Timeout(30.0, read=120.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach the model server at {self.base_url}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Model server returned {r.status_code}: {r.text[:300]}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as e:
            raise LLMError("Model server returned an invalid completion response") from e
        if not isinstance(content, str):
            raise LLMError("Model server returned a non-text completion")
        return content

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages, "stream": True}
        timeout = httpx.Timeout(30.0, read=120.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")[:300]
                        raise LLMError(f"Model server returned {r.status_code}: {body}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        choices = json.loads(data).get("choices") or []
                        if choices:
                            delta = choices[0].get("delta", {}).get("content")
                            if delta:
                                yield delta
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach the model server at {self.base_url}: {e}") from e
