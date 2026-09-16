"""Minimal HTTP transport shared by every NIM adapter.

The transport is deliberately small: JSON/multipart POST, JSON GET, the
standard NIM readiness probe and the OpenAPI document (which is how request
shapes are verified against a running container). Provider response bodies
never travel inside exceptions or logs — they can echo customer text.
"""

from __future__ import annotations

from typing import Any

import httpx


class NimError(RuntimeError):
    """The NIM container answered, but not with a usable result."""


class NimUnavailableError(NimError):
    """The NIM container could not be reached in time (fail-closed signal)."""


def nim_root(base_url: str) -> str:
    """Normalize ``http://host:8000`` / ``http://host:8000/v1`` to the container root."""

    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/")


class NimHttp:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        name: str = "nim",
    ) -> None:
        if not base_url.strip():
            raise ValueError(f"{name}: base URL is empty")
        self.root = nim_root(base_url)
        self.name = name
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    # --- helpers ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _timeout(self, override: float | None) -> httpx.Timeout:
        read = override if override is not None else self.timeout_seconds
        return httpx.Timeout(connect=5.0, read=read, write=10.0, pool=5.0)

    def url(self, path: str) -> str:
        return f"{self.root}/{path.lstrip('/')}"

    async def _send(self, build: Any, *, timeout_seconds: float | None) -> dict[str, Any]:
        """Run one request with a single retry on connection errors only."""

        attempts = 0
        while True:
            attempts += 1
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout(timeout_seconds),
                    follow_redirects=False,
                    headers=self._headers(),
                ) as client:
                    response: httpx.Response = await build(client)
                    response.raise_for_status()
                    data = response.json()
            except httpx.ConnectError as exc:
                if attempts < 2:
                    continue
                raise NimUnavailableError(f"{self.name}: container unreachable") from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise NimUnavailableError(f"{self.name}: request timed out") from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {502, 503, 504}:
                    raise NimUnavailableError(f"{self.name}: container not ready ({status})") from exc
                raise NimError(f"{self.name}: HTTP {status}") from exc
            except ValueError as exc:  # non-JSON body
                raise NimError(f"{self.name}: response is not JSON") from exc
            if not isinstance(data, dict):
                raise NimError(f"{self.name}: response is not an object")
            return data

    # --- public API ------------------------------------------------------

    async def post_json(
        self, path: str, payload: dict[str, Any], *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        url = self.url(path)
        return await self._send(
            lambda client: client.post(url, json=payload), timeout_seconds=timeout_seconds
        )

    async def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = self.url(path)
        return await self._send(
            lambda client: client.post(url, files=files, data=data or {}),
            timeout_seconds=timeout_seconds,
        )

    async def get_json(self, path: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        url = self.url(path)
        return await self._send(lambda client: client.get(url), timeout_seconds=timeout_seconds)

    async def ready(self) -> bool:
        """Standard NIM readiness probe; any failure is simply ``False``."""

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(5.0), follow_redirects=False, headers=self._headers()
            ) as client:
                response = await client.get(self.url("/v1/health/ready"))
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def openapi(self) -> dict[str, Any]:
        """The container's own contract — the source of truth for request shapes."""

        return await self.get_json("/v1/openapi.json")

    async def models(self) -> list[str]:
        """Model ids served by an OpenAI-style container (LLM, embedding, rerank)."""

        data = await self.get_json("/v1/models")
        items = data.get("data")
        if not isinstance(items, list):
            return []
        return [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
