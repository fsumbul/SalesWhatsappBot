"""Import all ORM models so Alembic autogenerate sees them.

Also imported by `main.py` at startup to ensure metadata is populated
before request handling.
"""

# ruff: noqa: F401  - imports are intentional
from src.modules.admin_chat import models as _admin_chat_models
from src.modules.admin_chat import outbound_models as _outbound_models
from src.modules.admin_chat import workflow_models as _workflow_models
from src.modules.agents import models as _agents_models
from src.modules.agents import runtime_models as _agent_runtime_models
from src.modules.agents import workspace_models as _workspace_models
from src.modules.auth import models as _auth_models
from src.modules.compliance import models as _compliance_models
from src.modules.discovery import models as _discovery_models
from src.modules.outreach import models as _outreach_models
from src.modules.sectors import models as _sectors_models
from src.modules.selection import models as _selection_models
