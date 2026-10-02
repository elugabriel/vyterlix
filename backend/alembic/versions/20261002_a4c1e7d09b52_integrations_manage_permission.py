"""integrations.manage permission

Revision ID: a4c1e7d09b52
Revises: de9fb7c59955
Create Date: 2026-10-02
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c1e7d09b52"
down_revision: str | None = "de9fb7c59955"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Connecting another system hands Vyterlix read access to that system's data, so only
    # the Owner may connect or disconnect one. (Viewing a connection and asking for a sync need
    # data.manage, which only the Owner holds today.)
    permission_id = uuid.uuid4()
    op.bulk_insert(
        sa.table(
            "permissions", sa.column("id", sa.Uuid), sa.column("code"), sa.column("description")
        ),
        [
            {
                "id": permission_id,
                "code": "integrations.manage",
                "description": "Connect and disconnect other systems (Xero, Shopify...)",
            }
        ],
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT id, :permission_id FROM roles "
            "WHERE code = 'owner' AND organization_id IS NULL"
        ).bindparams(permission_id=permission_id)
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE code = 'integrations.manage'")  # cascades to roles
