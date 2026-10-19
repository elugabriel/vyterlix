"""regular clients: the figure, and the win-back action answers it too

Revision ID: b2f7d4e91a36
Revises: fda574b1e2cf
Create Date: 2026-10-25
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2f7d4e91a36"
down_revision: str | None = "fda574b1e2cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _win_back_kpis() -> list[str]:
    row = (
        op.get_bind()
        .execute(sa.text("SELECT kpis FROM intervention_library WHERE code = 'win_back_regulars'"))
        .scalar()
    )
    return list(row or [])


def _set_win_back(kpis: list[str]) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE intervention_library SET kpis = CAST(:kpis AS json) "
            "WHERE code = 'win_back_regulars'"
        ),
        {"kpis": json.dumps(kpis)},
    )


def upgrade() -> None:
    kpi = sa.table(
        "kpi_definitions",
        sa.column("id", sa.Uuid), sa.column("code"), sa.column("name"), sa.column("description"),
        sa.column("category"), sa.column("unit"), sa.column("expression"), sa.column("direction"),
        sa.column("requires", sa.JSON), sa.column("sort_order", sa.SmallInteger),
    )  # fmt: skip
    op.bulk_insert(
        kpi,
        [
            {
                "id": uuid.uuid4(), "code": "regular_clients", "name": "Regular clients",
                "description": "How many clients kept three or more appointments in the last three months.",
                "category": "customer", "unit": "count", "expression": "regular_clients",
                "direction": "up_good", "requires": ["bookings"], "sort_order": 700,
            }
        ],
    )  # fmt: skip
    kpis = _win_back_kpis()
    if "regular_clients" not in kpis:
        _set_win_back([*kpis, "regular_clients"])


def downgrade() -> None:
    _set_win_back([k for k in _win_back_kpis() if k != "regular_clients"])
    op.execute("DELETE FROM kpi_definitions WHERE code = 'regular_clients'")
