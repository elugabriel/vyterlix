"""Model registry. Import every model module here so Alembic autogenerate sees it.

Tenant-owned tables must carry `organization_id` (see docs/adr/0001).
"""

from app.db import tenant  # noqa: F401  (registers tenant-isolation session hooks)
from app.db.base import Base
from app.models import (
    actions,
    admin,
    alerts,
    assistant,
    billing,
    business,
    data,
    diagnostics,
    forecast,
    health,
    identity,
    imports,
    integrations,
    jobs,
    kpi,
    memory,
    mobile,
    outcomes,
    recommendations,
    reports,
)

__all__ = [
    "Base",
    "actions",
    "admin",
    "alerts",
    "assistant",
    "billing",
    "business",
    "data",
    "diagnostics",
    "forecast",
    "health",
    "identity",
    "imports",
    "integrations",
    "jobs",
    "kpi",
    "memory",
    "mobile",
    "outcomes",
    "recommendations",
    "reports",
]
