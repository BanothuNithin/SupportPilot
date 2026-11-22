import hashlib
import re
from typing import Protocol

import httpx

from app.core.config import settings
from app.llm.base import LLMError

_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "and", "or", "for", "on", "what", "how", "do", "does",
         "can", "i", "my", "me", "you", "it", "be", "with", "this", "that", "at", "by", "from", "as", "was", "will"}


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic bag-of-words hashing vectors. Texts that share words get similar vectors,
    so retrieval is testable without any model."""

    DIM = 256

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in _STOP:
                continue
            if len(word) > 3 and word.endswith("s"):
                word = word[:-1]
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIM] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]


class OpenAICompatEmbedder:
    """Calls POST {base}/embeddings. Works with Ollama (nomic-embed-text), OpenAI and compatible servers."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
                r = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "input": texts},
                )
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach the embedding server at {self.base_url}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Embedding server returned {r.status_code}: {r.text[:300]}")
        items = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in items]


def get_embedder() -> Embedder:
    if settings.embed_provider == "openai_compat":
        return OpenAICompatEmbedder(settings.embed_base_url, settings.embed_api_key, settings.embed_model)
    return FakeEmbedder()
