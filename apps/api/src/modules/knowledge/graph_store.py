"""Thin async wrapper around the FalkorDB client.

Graph names encode tenant and agent version, so isolation is structural:
a query can only ever touch the graph its server-resolved scope names.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any
from uuid import UUID

from falkordb import FalkorDB

from src.core.config import get_settings


class GraphStore:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        self._db: Any | None = None

    # --- naming -----------------------------------------------------------

    @staticmethod
    def kb_graph_name(tenant_id: UUID, agent_version_id: UUID) -> str:
        return f"kb_{tenant_id.hex}_{agent_version_id.hex}"

    @staticmethod
    def memory_graph_name(tenant_id: UUID) -> str:
        return f"mem_{tenant_id.hex}"

    # --- sync internals (run in threads) ------------------------------------

    def _client(self) -> Any:
        if self._db is None:
            self._db = FalkorDB(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
            )
        return self._db

    def _query_sync(
        self, graph_name: str, cypher: str, params: dict[str, Any] | None
    ) -> list[list[Any]]:
        graph = self._client().select_graph(graph_name)
        result = graph.query(cypher, params or {})
        return [list(row) for row in result.result_set]

    def _list_graphs_sync(self) -> list[str]:
        return [str(name) for name in self._client().list_graphs()]

    def _delete_graph_sync(self, graph_name: str) -> None:
        self._client().select_graph(graph_name).delete()

    def _ping_sync(self) -> bool:
        return bool(self._client().connection.ping())

    # --- async surface --------------------------------------------------------

    async def query(
        self, graph_name: str, cypher: str, params: dict[str, Any] | None = None
    ) -> list[list[Any]]:
        return await asyncio.to_thread(self._query_sync, graph_name, cypher, params)

    async def graph_exists(self, graph_name: str) -> bool:
        return graph_name in await asyncio.to_thread(self._list_graphs_sync)

    async def list_graphs(self) -> list[str]:
        return await asyncio.to_thread(self._list_graphs_sync)

    async def delete_graph(self, graph_name: str) -> None:
        if await self.graph_exists(graph_name):
            await asyncio.to_thread(self._delete_graph_sync, graph_name)

    async def ping(self) -> bool:
        try:
            return await asyncio.to_thread(self._ping_sync)
        except Exception:
            return False


@lru_cache
def get_graph_store() -> GraphStore:
    s = get_settings()
    return GraphStore(
        host=s.falkordb_host,
        port=s.falkordb_port,
        username=s.falkordb_username,
        password=s.falkordb_password,
    )
