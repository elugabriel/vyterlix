"""goals.manage permission

Revision ID: 032403888c06
Revises: 97b99f43f225
Create Date: 2026-09-26
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032403888c06"
down_revision: str | None = "97b99f43f225"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Owners and Managers set and track business goals; Managers only within their remit.
    permission_id = uuid.uuid4()
    op.bulk_insert(
        sa.table(
            "permissions", sa.column("id", sa.Uuid), sa.column("code"), sa.column("description")
        ),
        [
            {
                "id": permission_id,
                "code": "goals.manage",
                "description": "Create and update business goals (managers: within remit)",
            }
        ],
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT id, :permission_id FROM roles "
            "WHERE code IN ('owner', 'manager') AND organization_id IS NULL"
        ).bindparams(permission_id=permission_id)
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE code = 'goals.manage'")  # cascades to roles
