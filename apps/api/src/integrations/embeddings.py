"""Embedding clients for knowledge retrieval (ADR-002).

Embeddings are a reproducible, derived index over *approved customer-visible*
fact text. They carry no company authority: a nearest neighbour is only a
candidate for the trusted runtime, never an answer. The model name and
dimension are deployment settings (an "embedding profile"), never code
constants, so a model change re-indexes instead of silently mixing spaces.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from src.core.config import get_settings

_BATCH_SIZE = 32


class EmbeddingError(RuntimeError):
    """The embedding service did not return usable vectors."""


class EmbeddingNotConfiguredError(EmbeddingError):
    """No embedding provider is configured (``EMBEDDING_PROVIDER`` empty)."""


class EmbeddingClient(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class NullEmbeddingClient:
    """Fail closed instead of pretending an index exists."""

    model_name = ""
    dimension = 0

    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingNotConfiguredError("EMBEDDING_PROVIDER is empty")

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingNotConfiguredError("EMBEDDING_PROVIDER is empty")


class OllamaEmbeddingClient:
    """BGE-M3 (or any Ollama embedding model) through the native ``/api/embed``.

    BGE-M3 is multilingual with strong Turkish coverage and needs no query
    instruction prefix, so queries and documents share one code path.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimension: int,
        read_timeout_seconds: float = 60.0,
    ) -> None:
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        self.api_url = f"{root.rstrip('/')}/api/embed"
        self.model_name = model
        self.dimension = dimension
        self._timeout = httpx.Timeout(connect=5.0, read=read_timeout_seconds, write=10.0, pool=5.0)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text])
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(await self._embed(texts[start : start + _BATCH_SIZE]))
        return vectors

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        payload: dict[str, object] = {
            "model": self.model_name,
            "input": inputs,
            "truncate": True,
            "keep_alive": "30m",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise TypeError("embedding response must be an object")
            vectors = data.get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(inputs):
                raise TypeError("embedding response count mismatch")
            result: list[list[float]] = []
            for vector in vectors:
                if not isinstance(vector, list) or len(vector) != self.dimension:
                    raise TypeError("embedding dimension mismatch")
                result.append([float(value) for value in vector])
            return result
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            # Provider bodies may echo customer text; never surface them.
            raise EmbeddingError("The embedding service did not return usable vectors") from exc


def get_embedding_client() -> EmbeddingClient:
    """Factory keyed on settings; the single place a provider is chosen."""

    s = get_settings()
    if s.embedding_provider == "ollama":
        return OllamaEmbeddingClient(
            base_url=s.embedding_base_url or s.llm_base_url,
            model=s.embedding_model,
            dimension=s.embedding_dimension,
        )
    return NullEmbeddingClient()
