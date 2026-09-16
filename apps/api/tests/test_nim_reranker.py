"""NIM reranking adapter (NIM plan WP4): logits → [0, 1], missing passages score 0."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from src.integrations.reranker import (
    CrossEncoderReranker,
    NimReranker,
    NullReranker,
    RerankError,
    get_reranker,
)
from src.modules.agents.grounded_audit import CrossEncoderEntailment

_FIXTURES = Path(__file__).parent / "fixtures" / "nim"


def _fixture(name: str) -> dict[str, Any]:
    payload = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    return dict(payload)


@respx.mock
async def test_nim_reranker_maps_logits_to_probabilities_in_document_order() -> None:
    route = respx.post("http://gpu:8041/v1/ranking").mock(
        return_value=httpx.Response(200, json=_fixture("ranking.json"))
    )
    reranker = NimReranker(base_url="http://gpu:8041/v1", model="nvidia/llama-nemotron-rerank-vl-1b-v2")
    scores = await reranker.rerank("6211 rulman", ["a", "b", "c"])

    assert scores == pytest.approx([1 / (1 + math.exp(1.0)), 0.0, 1 / (1 + math.exp(-2.0))])
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "model": "nvidia/llama-nemotron-rerank-vl-1b-v2",
        "query": {"text": "6211 rulman"},
        "passages": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        "truncate": "END",
    }
    assert await reranker.rerank("x", []) == []
    assert reranker.available and reranker.model_name == "nvidia/llama-nemotron-rerank-vl-1b-v2"


@respx.mock
async def test_nim_reranker_fails_closed_on_bad_shapes() -> None:
    respx.post("http://gpu:8041/v1/ranking").mock(
        return_value=httpx.Response(200, json={"rankings": [{"index": 7, "logit": 1.0}]})
    )
    reranker = NimReranker(base_url="http://gpu:8041", model="m")
    with pytest.raises(RerankError):
        await reranker.rerank("q", ["a"])
    respx.post("http://gpu:8041/v1/ranking").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(RerankError):
        await reranker.rerank("q", ["a"])


@respx.mock
async def test_nim_reranker_serves_the_entailment_gate_on_the_same_scale() -> None:
    respx.post("http://gpu:8041/v1/ranking").mock(
        return_value=httpx.Response(200, json={"rankings": [{"index": 0, "logit": 0.0}, {"index": 1, "logit": 3.0}]})
    )
    verifier = CrossEncoderEntailment(NimReranker(base_url="http://gpu:8041", model="m"))
    score = await verifier.score("6211 rulman 55 mm mile uygundur", ["55 mm mil", "6211 rulman 55 mm"])
    assert score == pytest.approx(1 / (1 + math.exp(-3.0)))
    assert verifier.backend == "m" and verifier.available


def test_factory_picks_provider_from_settings() -> None:
    get_reranker.cache_clear()
    try:
        with patch("src.integrations.reranker.get_settings") as mock_settings:
            mock_settings.return_value.reranker_enabled = True
            mock_settings.return_value.reranker_provider = "nim"
            mock_settings.return_value.reranker_base_url = "http://10.0.0.5:8041"
            mock_settings.return_value.reranker_model = "nvidia/llama-nemotron-rerank-vl-1b-v2"
            mock_settings.return_value.reranker_api_key = ""
            mock_settings.return_value.nim_api_key = ""
            mock_settings.return_value.nim_timeout_seconds = 10.0
            assert isinstance(get_reranker(), NimReranker)
        get_reranker.cache_clear()
        with patch("src.integrations.reranker.get_settings") as mock_settings:
            mock_settings.return_value.reranker_enabled = True
            mock_settings.return_value.reranker_provider = "nim"
            mock_settings.return_value.reranker_base_url = ""
            assert isinstance(get_reranker(), NullReranker)
        get_reranker.cache_clear()
        with patch("src.integrations.reranker.get_settings") as mock_settings:
            mock_settings.return_value.reranker_enabled = True
            mock_settings.return_value.reranker_provider = "local"
            mock_settings.return_value.reranker_model = "BAAI/bge-reranker-v2-m3"
            mock_settings.return_value.reranker_device = "cpu"
            assert isinstance(get_reranker(), CrossEncoderReranker)
    finally:
        get_reranker.cache_clear()
