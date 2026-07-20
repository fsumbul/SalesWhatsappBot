"""Import all ORM models so Alembic autogenerate sees them.

Also imported by `main.py` at startup to ensure metadata is populated
before request handling.
"""

# ruff: noqa: F401  - imports are intentional
from src.modules.auth import models as _auth_models
from src.modules.compliance import models as _compliance_models
from src.modules.discovery import models as _discovery_models
from src.modules.outreach import models as _outreach_models
from src.modules.sectors import models as _sectors_models
