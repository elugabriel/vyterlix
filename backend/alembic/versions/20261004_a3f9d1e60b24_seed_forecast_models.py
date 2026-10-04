"""seed the forecasting methods

Revision ID: a3f9d1e60b24
Revises: c7de7630cc0f
Create Date: 2026-10-04
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9d1e60b24"
down_revision: str | None = "c7de7630cc0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, description, min_history). The maths is in app/forecast/models.py; this table
# says which methods exist so a forecast can record which one made it.
MODELS = [
    (
        "moving_average",
        "Recent average",
        "Expects the next months to be like the average of the last three.",
        3,
    ),
    (
        "linear_trend",
        "Straight-line trend",
        "Draws a straight line through the last twelve months and carries it forward.",
        4,
    ),
    (
        "seasonal_naive",
        "Same month last year",
        "Expects each month to repeat the same month a year earlier.",
        12,
    ),
]


def upgrade() -> None:
    table = sa.table(
        "forecast_models",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("version", sa.String),
        sa.column("min_history", sa.SmallInteger),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "description": description,
                "version": "1",
                "min_history": min_history,
                "is_active": True,
            }
            for code, name, description, min_history in MODELS
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM forecast_models")
