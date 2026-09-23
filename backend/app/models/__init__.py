"""Model registry. Import every model module here so Alembic autogenerate sees it.

Tenant-owned tables must carry `organization_id` (see docs/adr/0001).
"""

from app.db.base import Base

__all__ = ["Base"]
