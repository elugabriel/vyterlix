"""Model registry. Import every model module here so Alembic autogenerate sees it.

Tenant-owned tables must carry `organization_id` (see docs/adr/0001).
"""

from app.db import tenant  # noqa: F401  (registers tenant-isolation session hooks)
from app.db.base import Base
from app.models import business, identity

__all__ = ["Base", "business", "identity"]
