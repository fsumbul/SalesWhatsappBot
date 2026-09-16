"""Embedding clients for knowledge retrieval (ADR-002).

Embeddings are a reproducible, derived index over *approved customer-visible*
fact text. They carry no company authority: a nearest neighbour is only a
candidate for the trusted runtime, never an answer. The model name and
dimension are deployment settings (an "embedding profile"), never code
constants, so a model change re-indexes instead of silently mixing spaces.
"""

from __future__ import annotations

from typing import Literal, Protocol

import httpx

from src.core.config import get_settings

_BATCH_SIZE = 32

# Native output sizes of the NeMo Retriever embedding NIMs (plan §2). Only the
# truncatable models accept a smaller ``dimensions`` value.
NIM_NATIVE_DIMENSIONS: dict[str, int] = {
    "nvidia/nemotron-3-embed-1b": 2048,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
    "nvidia/llama-3.2-nv-embedqa-1b-v2": 2048,
}
NIM_TRUNCATABLE_MODELS = frozenset(
    {"nvidia/llama-nemotron-embed-vl-1b-v2", "nvidia/llama-3.2-nv-embedqa-1b-v2"}
)


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


def embedding_profile(client: EmbeddingClient) -> str:
    """Identity of an embedding space for index fingerprints (model + dimension).

    Two clients with the same model name but different output sizes must never
    share a graph; the dimension is therefore part of every fingerprint.
    """

    return f"{client.model_name}#{client.dimension}"


def nim_embedding_dimension_error(model: str, dimension: int) -> str | None:
    """Explain why ``dimension`` cannot be requested from a NIM embedding model."""

    native = NIM_NATIVE_DIMENSIONS.get(model)
    if native is None or dimension == native:
        return None
    if model in NIM_TRUNCATABLE_MODELS and 0 < dimension < native:
        return None
    return (
        f"EMBEDDING_DIMENSION must be {native} for {model} "
        "(the model does not support that output size)"
    )


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


class NimEmbeddingClient:
    """NeMo Retriever embedding NIM through the OpenAI-style ``/v1/embeddings``.

    Queries and passages are embedded asymmetrically (``input_type``), inputs
    are truncated server-side (``truncate=END``) and ``dimensions`` is only sent
    for models that support Matryoshka truncation. The response size is
    verified against the configured dimension so a misconfigured profile can
    never write vectors of the wrong length into a graph.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimension: int,
        api_key: str = "",
        read_timeout_seconds: float = 60.0,
    ) -> None:
        from src.integrations.nim import NimHttp

        self.http = NimHttp(
            base_url, api_key=api_key, timeout_seconds=read_timeout_seconds, name="embedding"
        )
        self.model_name = model
        self.dimension = dimension
        native = NIM_NATIVE_DIMENSIONS.get(model)
        self._send_dimensions = (
            model in NIM_TRUNCATABLE_MODELS and native is not None and dimension != native
        )

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], "query")
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(await self._embed(texts[start : start + _BATCH_SIZE], "passage"))
        return vectors

    async def _embed(
        self, inputs: list[str], input_type: Literal["query", "passage"]
    ) -> list[list[float]]:
        if not inputs:
            return []
        payload: dict[str, object] = {
            "model": self.model_name,
            "input": inputs,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        }
        if self._send_dimensions:
            payload["dimensions"] = self.dimension
        try:
            data = await self.http.post_json("/v1/embeddings", payload)
            items = data.get("data")
            if not isinstance(items, list) or len(items) != len(inputs):
                raise TypeError("embedding response count mismatch")
            ordered: list[list[float] | None] = [None] * len(inputs)
            for position, item in enumerate(items):
                if not isinstance(item, dict):
                    raise TypeError("embedding item must be an object")
                index = item.get("index", position)
                vector = item.get("embedding")
                if (
                    not isinstance(index, int)
                    or not 0 <= index < len(inputs)
                    or not isinstance(vector, list)
                    or len(vector) != self.dimension
                ):
                    raise TypeError("embedding dimension mismatch")
                ordered[index] = [float(value) for value in vector]
            if any(vector is None for vector in ordered):
                raise TypeError("embedding response is missing inputs")
            return [vector for vector in ordered if vector is not None]
        except Exception as exc:
            # NimError / TypeError / ValueError: never surface provider bodies.
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
    if s.embedding_provider == "nim" and s.embedding_base_url.strip():
        return NimEmbeddingClient(
            base_url=s.embedding_base_url,
            model=s.embedding_model,
            dimension=s.embedding_dimension,
            api_key=s.embedding_api_key or s.nim_api_key,
        )
    return NullEmbeddingClient()
