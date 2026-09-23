"""audit log append-only and audit.view permission

Revision ID: cee5c110fba7
Revises: c8bd9423d7ad
Create Date: 2026-09-23 21:58:28.082096
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cee5c110fba7"
down_revision: str | None = "c8bd9423d7ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rows can't be edited or deleted. The only permitted UPDATE is the database itself
# blanking actor_user_id / organization_id when a user or organisation is deleted
# (ON DELETE SET NULL), so the history survives. Retention clean-up must explicitly
# `SET LOCAL vyterlix.allow_audit_delete = 'on'` inside its transaction.
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('vyterlix.allow_audit_delete', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'audit_logs is append-only: rows cannot be deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF (NEW.id, NEW.action, NEW.target_type, NEW.target_id, NEW.ip_address,
        NEW.user_agent, NEW.details, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.action, OLD.target_type, OLD.target_id, OLD.ip_address,
        OLD.user_agent, OLD.details, OLD.created_at)
       OR (NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
           AND NEW.actor_user_id IS NOT NULL)
       OR (NEW.organization_id IS DISTINCT FROM OLD.organization_id
           AND NEW.organization_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'audit_logs is append-only: rows cannot be edited'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    # Real write time, so entries from one transaction keep their order.
    op.alter_column("audit_logs", "created_at", server_default=sa.text("clock_timestamp()"))
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )

    # New permission: read the organisation's audit log. Owners only.
    permission_id = uuid.uuid4()
    op.bulk_insert(
        sa.table(
            "permissions", sa.column("id", sa.Uuid), sa.column("code"), sa.column("description")
        ),
        [
            {
                "id": permission_id,
                "code": "audit.view",
                "description": "View the organisation's audit log",
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
    op.execute("DELETE FROM permissions WHERE code = 'audit.view'")  # cascades to role mapping
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION audit_logs_append_only()")
    op.alter_column("audit_logs", "created_at", server_default=sa.text("now()"))
