"""NIM embedding adapter and embedding-profile fingerprints (NIM plan WP4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from src.core.config import Settings
from src.integrations.embeddings import (
    EmbeddingError,
    NimEmbeddingClient,
    NullEmbeddingClient,
    OllamaEmbeddingClient,
    embedding_profile,
    get_embedding_client,
    nim_embedding_dimension_error,
)
from src.modules.knowledge.compiler import index_fingerprint

_FIXTURES = Path(__file__).parent / "fixtures" / "nim"
_STRONG_SECRET = "fK9!vT2@qL7#sN4$wR8%mC5^xP1&zD6*"


def _fixture(name: str) -> dict[str, Any]:
    payload = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    return dict(payload)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "production",
        "app_debug": False,
        "app_secret_key": _STRONG_SECRET,
        "database_url": "postgresql+asyncpg://app:pass@db/app",
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/1",
        "celery_result_backend": "redis://redis:6379/2",
        "whatsapp_app_secret": "s",
        "whatsapp_access_token": "t",
        "whatsapp_verify_token": "v",
        "whatsapp_phone_number_id": "p",
        "whatsapp_business_account_id": "w",
        "llm_provider": "ollama",
        "llm_model": "qwen3:8b",
        "llm_base_url": "http://127.0.0.1:11434/v1",
    }
    values.update(overrides)
    return Settings(**values)


@respx.mock
async def test_nim_embeddings_send_input_type_and_reorder_by_index() -> None:
    route = respx.post("http://gpu:8040/v1/embeddings").mock(
        return_value=httpx.Response(200, json=_fixture("embeddings_passage.json"))
    )
    client = NimEmbeddingClient(base_url="http://gpu:8040/v1", model="nvidia/nemotron-3-embed-1b", dimension=3)

    vectors = await client.embed_documents(["Döküm kasnak", "Naylon kasnak"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]  # index 0 first even though listed second
    body = json.loads(route.calls.last.request.content)
    assert body["input_type"] == "passage" and body["truncate"] == "END"
    assert body["encoding_format"] == "float" and "dimensions" not in body

    respx.post("http://gpu:8040/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]})
    )
    assert await client.embed_query("6211 rulman") == [1.0, 0.0, 0.0]
    assert json.loads(respx.calls.last.request.content)["input_type"] == "query"
    assert await client.embed_documents([]) == []


@respx.mock
async def test_nim_embeddings_send_dimensions_only_for_truncatable_models() -> None:
    respx.post("http://gpu:8040/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    )
    truncatable = NimEmbeddingClient(base_url="http://gpu:8040", model="nvidia/llama-nemotron-embed-vl-1b-v2", dimension=2)
    await truncatable.embed_query("x")
    assert json.loads(respx.calls.last.request.content)["dimensions"] == 2
    fixed = NimEmbeddingClient(base_url="http://gpu:8040", model="nvidia/nemotron-3-embed-1b", dimension=2)
    await fixed.embed_query("x")
    assert "dimensions" not in json.loads(respx.calls.last.request.content)


@respx.mock
async def test_nim_embeddings_fail_closed_on_shape_or_transport_errors() -> None:
    respx.post("http://gpu:8040/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}]})
    )
    client = NimEmbeddingClient(base_url="http://gpu:8040", model="nvidia/nemotron-3-embed-1b", dimension=3)
    with pytest.raises(EmbeddingError):
        await client.embed_query("x")
    respx.post("http://gpu:8040/v1/embeddings").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(EmbeddingError):
        await client.embed_query("x")
    respx.post("http://gpu:8040/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})
    )
    with pytest.raises(EmbeddingError):  # two inputs, one vector
        await client.embed_documents(["a", "b"])


def test_embedding_profile_is_part_of_every_fingerprint() -> None:
    small = NimEmbeddingClient(base_url="http://gpu:8040", model="nvidia/llama-nemotron-embed-vl-1b-v2", dimension=1024)
    large = NimEmbeddingClient(base_url="http://gpu:8040", model="nvidia/llama-nemotron-embed-vl-1b-v2", dimension=2048)
    assert embedding_profile(small) == "nvidia/llama-nemotron-embed-vl-1b-v2#1024"
    documents = [("f1", "Döküm kasnak 320 mm")]
    assert index_fingerprint(documents, embedding_profile(small)) != index_fingerprint(documents, embedding_profile(large))
    ollama = OllamaEmbeddingClient(base_url="http://127.0.0.1:11434", model="bge-m3", dimension=1024)
    assert embedding_profile(ollama) == "bge-m3#1024"


def test_dimension_rules_for_nim_models() -> None:
    assert nim_embedding_dimension_error("nvidia/nemotron-3-embed-1b", 2048) is None
    assert nim_embedding_dimension_error("nvidia/nemotron-3-embed-1b", 1024) == (
        "EMBEDDING_DIMENSION must be 2048 for nvidia/nemotron-3-embed-1b (the model does not support that output size)"
    )
    assert nim_embedding_dimension_error("nvidia/llama-nemotron-embed-vl-1b-v2", 512) is None
    assert nim_embedding_dimension_error("nvidia/llama-nemotron-embed-vl-1b-v2", 4096) is not None
    assert nim_embedding_dimension_error("some/custom-model", 768) is None


def test_settings_accept_nim_embeddings_and_gate_the_profile() -> None:
    ok = _settings(
        knowledge_backend="falkordb",
        embedding_provider="nim",
        embedding_model="nvidia/nemotron-3-embed-1b",
        embedding_dimension=2048,
        embedding_base_url="http://10.0.0.5:8040/v1",
    )
    assert ok.production_runtime_errors() == []
    endpoint = next(e for e in ok.model_endpoints() if e.role == "embedding")
    assert endpoint.nim and endpoint.url == "http://10.0.0.5:8040/v1"

    wrong_dimension = _settings(
        embedding_provider="nim",
        embedding_model="nvidia/nemotron-3-embed-1b",
        embedding_dimension=1024,
        embedding_base_url="http://10.0.0.5:8040/v1",
    )
    assert any(e.startswith("EMBEDDING_DIMENSION must be 2048") for e in wrong_dimension.production_runtime_errors())

    missing_url = _settings(embedding_provider="nim", embedding_model="nvidia/nemotron-3-embed-1b", embedding_dimension=2048, embedding_base_url="")
    assert "EMBEDDING_BASE_URL is required" in missing_url.production_runtime_errors()

    lexical_only = _settings(knowledge_backend="falkordb", embedding_provider="")
    assert "EMBEDDING_PROVIDER must be ollama or nim when KNOWLEDGE_BACKEND=falkordb" in lexical_only.production_runtime_errors()


def test_factory_chooses_the_nim_client_from_settings() -> None:
    with patch("src.integrations.embeddings.get_settings") as mock_settings:
        mock_settings.return_value.embedding_provider = "nim"
        mock_settings.return_value.embedding_base_url = "http://10.0.0.5:8040"
        mock_settings.return_value.embedding_model = "nvidia/nemotron-3-embed-1b"
        mock_settings.return_value.embedding_dimension = 2048
        mock_settings.return_value.embedding_api_key = ""
        mock_settings.return_value.nim_api_key = "shared"
        client = get_embedding_client()
    assert isinstance(client, NimEmbeddingClient) and client.dimension == 2048
    assert client.http.api_key == "shared"

    with patch("src.integrations.embeddings.get_settings") as mock_settings:
        mock_settings.return_value.embedding_provider = "nim"
        mock_settings.return_value.embedding_base_url = ""
        assert isinstance(get_embedding_client(), NullEmbeddingClient)
