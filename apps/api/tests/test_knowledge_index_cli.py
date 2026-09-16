"""The indexing CLI must materialize rows before tenant cleanup expires them."""

import pytest

from scripts.knowledge_index import _live_versions
from tests.test_whatsapp_runtime_integration import (
    _seed_runtime_tenant,
    runtime_database,  # noqa: F401 - shared real-Postgres fixture
)


@pytest.mark.asyncio
async def test_live_versions_survive_tenant_context_reset(runtime_database: None) -> None:  # noqa: F811
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    versions = await _live_versions(f"runtime-integration-{tenant_id.hex}")
    assert len(versions) == 1
    result_tenant, version_id, config = versions[0]
    assert result_tenant == tenant_id
    assert version_id is not None
    assert config.agent is not None
