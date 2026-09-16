"""Shared NIM transport: headers, retry policy, fail-closed errors, no body leaks."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.integrations.nim import NimError, NimHttp, NimUnavailableError
from src.integrations.nim.http import nim_root


def test_root_normalizes_v1_suffix() -> None:
    assert nim_root("http://gpu:8000/v1/") == "http://gpu:8000"
    assert nim_root("http://gpu:8000") == "http://gpu:8000"
    assert NimHttp("http://gpu:8000/v1").url("/v1/classify") == "http://gpu:8000/v1/classify"


def test_empty_base_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        NimHttp("   ", name="guard")


@respx.mock
async def test_bearer_header_only_when_key_is_set() -> None:
    route = respx.post("http://gpu:8000/v1/classify").mock(
        return_value=httpx.Response(200, json={"jailbreak": False, "score": -0.9})
    )
    await NimHttp("http://gpu:8000", api_key="ngc-key").post_json("/v1/classify", {"input": "x"})
    assert route.calls.last.request.headers["Authorization"] == "Bearer ngc-key"

    await NimHttp("http://gpu:8000").post_json("/v1/classify", {"input": "x"})
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_single_retry_on_connection_error_then_unavailable() -> None:
    route = respx.post("http://gpu:8000/v1/classify").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(NimUnavailableError):
        await NimHttp("http://gpu:8000").post_json("/v1/classify", {"input": "x"})
    assert route.call_count == 2


@respx.mock
async def test_timeout_is_unavailable_without_retry() -> None:
    route = respx.post("http://gpu:8000/v1/classify").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(NimUnavailableError):
        await NimHttp("http://gpu:8000").post_json("/v1/classify", {"input": "x"})
    assert route.call_count == 1


@respx.mock
async def test_http_error_never_carries_the_response_body() -> None:
    respx.post("http://gpu:8000/v1/classify").mock(
        return_value=httpx.Response(400, text="echo of a customer message 0532 111 22 33")
    )
    with pytest.raises(NimError) as info:
        await NimHttp("http://gpu:8000", name="guard").post_json("/v1/classify", {"input": "x"})
    assert "0532" not in str(info.value)
    assert "HTTP 400" in str(info.value)


@respx.mock
async def test_gateway_errors_are_unavailable() -> None:
    respx.post("http://gpu:8000/v1/classify").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(NimUnavailableError):
        await NimHttp("http://gpu:8000").post_json("/v1/classify", {"input": "x"})


@respx.mock
async def test_non_json_and_non_object_bodies_fail_closed() -> None:
    respx.post("http://gpu:8000/v1/a").mock(return_value=httpx.Response(200, text="<html>"))
    respx.post("http://gpu:8000/v1/b").mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(NimError):
        await NimHttp("http://gpu:8000").post_json("/v1/a", {})
    with pytest.raises(NimError):
        await NimHttp("http://gpu:8000").post_json("/v1/b", {})


@respx.mock
async def test_ready_and_models_helpers() -> None:
    respx.get("http://gpu:8000/v1/health/ready").mock(return_value=httpx.Response(200, json={}))
    respx.get("http://gpu:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-3-embed-1b"}]})
    )
    http = NimHttp("http://gpu:8000/v1")
    assert await http.ready() is True
    assert await http.models() == ["nvidia/nemotron-3-embed-1b"]

    respx.get("http://gpu:8001/v1/health/ready").mock(side_effect=httpx.ConnectError("down"))
    assert await NimHttp("http://gpu:8001").ready() is False
