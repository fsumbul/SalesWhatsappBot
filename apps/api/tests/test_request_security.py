"""Focused guards for trusted proxy handling and bounded private API limits."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.core import deps, request_rate_limit


def _request(peer: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": (peer, 1)})


def test_untrusted_peer_cannot_choose_an_xff_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "get_settings", lambda: SimpleNamespace(trusted_proxy_cidrs_list=["10.0.0.0/8"])
    )

    assert deps.get_client_ip(_request("198.51.100.7"), "203.0.113.9") == "198.51.100.7"


def test_trusted_proxy_can_supply_only_a_valid_xff_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "get_settings", lambda: SimpleNamespace(trusted_proxy_cidrs_list=["10.0.0.0/8"])
    )

    assert deps.get_client_ip(_request("10.1.2.3"), "203.0.113.9, 10.1.2.3") == "203.0.113.9"
    assert deps.get_client_ip(_request("10.1.2.3"), "not-an-ip") == "10.1.2.3"


def test_trusted_proxy_uses_rightmost_untrusted_xff_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs_list=["10.0.0.0/8", "192.0.2.0/24"]),
    )

    # A public client can append a fake first value before the ingress appends
    # the verified source and an internal proxy. Only the source next to the
    # trusted proxy chain is usable for rate-limit/audit identity.
    assert (
        deps.get_client_ip(
            _request("10.1.2.3"), "198.51.100.77, 203.0.113.9, 192.0.2.8"
        )
        == "203.0.113.9"
    )


@pytest.mark.asyncio
async def test_private_limit_uses_an_opaque_redis_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, float]] = []

    class Bucket:
        def __init__(self, key: str, *, capacity: int, refill_per_sec: float) -> None:
            calls.append((key, capacity, refill_per_sec))

        async def acquire(self) -> float:
            return 0

    monkeypatch.setattr(
        request_rate_limit, "get_settings", lambda: SimpleNamespace(api_rate_limit_enabled=True)
    )
    monkeypatch.setattr(request_rate_limit, "TokenBucket", Bucket)

    await request_rate_limit.enforce_request_rate_limit("login_account", "tenant:user@example.com")

    assert len(calls) == 1
    key, capacity, refill = calls[0]
    assert (capacity, refill) == (10, 10 / 60)
    assert "user@example.com" not in key
    assert key.startswith("private_api:login_account:")


@pytest.mark.asyncio
async def test_private_limit_returns_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bucket:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def acquire(self) -> float:
            return 1.1

    monkeypatch.setattr(
        request_rate_limit, "get_settings", lambda: SimpleNamespace(api_rate_limit_enabled=True)
    )
    monkeypatch.setattr(request_rate_limit, "TokenBucket", Bucket)

    with pytest.raises(HTTPException) as raised:
        await request_rate_limit.enforce_request_rate_limit("campaign_import", "tenant:user:ip")

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "2"}
