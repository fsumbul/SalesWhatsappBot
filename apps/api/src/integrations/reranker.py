"""Cross-encoder reranking for retrieval candidates (ADR-002).

``BAAI/bge-reranker-v2-m3`` scores (query, approved fact text) pairs jointly,
which is far more precise for Turkish paraphrases and typos than bi-encoder
similarity alone. It runs in-process (Apple MPS / CUDA / CPU) inside the
worker, is loaded lazily once per process, and is optional: when disabled or
failing, fused first-stage ranking is used unchanged.
"""

from __future__ import annotations

import asyncio
import math
import threading
from functools import lru_cache
from typing import Any, Protocol

from src.core.config import get_settings


class RerankError(RuntimeError):
    """The reranker could not score the candidate pool."""


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def available(self) -> bool: ...

    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class NullReranker:
    model_name = ""
    available = False

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.0 for _ in documents]


def _resolve_device(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is an optional heavy dependency
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class CrossEncoderReranker:
    """sentence-transformers ``CrossEncoder`` wrapper (sigmoid scores in [0, 1])."""

    available = True

    def __init__(self, *, model: str, device: str = "auto", max_length: int = 512) -> None:
        self.model_name = model
        self._device = device
        self._max_length = max_length
        self._model: Any | None = None
        self._lock = threading.Lock()
        # Concurrent turns rerank from separate threads; MPS/CUDA inference is
        # not re-entrant (Metal command buffers abort the process), so scoring
        # is serialized. A 24-pair batch takes ~150 ms, so contention is small.
        self._predict_lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(
                    self.model_name,
                    device=_resolve_device(self._device),
                    max_length=self._max_length,
                )
            return self._model

    def _predict(self, query: str, documents: list[str]) -> list[float]:
        model = self._load()
        with self._predict_lock:
            scores = model.predict(
                [(query, document) for document in documents],
                batch_size=16,
                show_progress_bar=False,
            )
        return [float(score) for score in scores]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        try:
            return await asyncio.to_thread(self._predict, query, documents)
        except Exception as exc:
            raise RerankError("The reranker failed to score candidates") from exc


class NimReranker:
    """NeMo Retriever reranking NIM (``POST /v1/ranking``).

    The container returns logits; they are squashed with a sigmoid so the
    scores live on the same [0, 1] scale as the in-process cross-encoder and
    the entailment threshold / graph bonuses of ADR-002/ADR-003 keep their
    meaning. Passages missing from the answer score 0.
    """

    available = True

    def __init__(
        self, *, base_url: str, model: str, api_key: str = "", timeout_seconds: float = 10.0
    ) -> None:
        from src.integrations.nim import NimHttp

        self.http = NimHttp(
            base_url, api_key=api_key, timeout_seconds=timeout_seconds, name="reranker"
        )
        self.model_name = model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        payload: dict[str, object] = {
            "model": self.model_name,
            "query": {"text": query},
            "passages": [{"text": document} for document in documents],
            "truncate": "END",
        }
        try:
            data = await self.http.post_json("/v1/ranking", payload)
            rankings = data.get("rankings")
            if not isinstance(rankings, list):
                raise TypeError("ranking response has no rankings")
            scores = [0.0] * len(documents)
            for item in rankings:
                if not isinstance(item, dict):
                    raise TypeError("ranking item must be an object")
                index = item.get("index")
                logit = item.get("logit", item.get("score"))
                if (
                    not isinstance(index, int)
                    or isinstance(logit, bool)
                    or not isinstance(logit, int | float)
                    or not 0 <= index < len(documents)
                ):
                    raise TypeError("ranking item has an unexpected shape")
                scores[index] = 1.0 / (1.0 + math.exp(-float(logit)))
            return scores
        except Exception as exc:
            raise RerankError("The reranker failed to score candidates") from exc


@lru_cache
def get_reranker() -> Reranker:
    """Process-wide reranker so the model loads once per worker."""

    s = get_settings()
    if not s.reranker_enabled:
        return NullReranker()
    if s.reranker_provider == "nim":
        if not s.reranker_base_url.strip():
            return NullReranker()
        return NimReranker(
            base_url=s.reranker_base_url,
            model=s.reranker_model,
            api_key=s.reranker_api_key or s.nim_api_key,
            timeout_seconds=s.nim_timeout_seconds,
        )
    return CrossEncoderReranker(model=s.reranker_model, device=s.reranker_device)
