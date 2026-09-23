"""baseline

Revision ID: 62aef4fee9a8
Revises:
Create Date: 2026-09-23 10:38:12.952214
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "62aef4fee9a8"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
