"""temporary actor priority boost marker"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260412_0007"
down_revision = "20260411_0006"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("tracked_actors")
    if "priority_boosted_at" not in columns:
        op.add_column("tracked_actors", sa.Column("priority_boosted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = _column_names("tracked_actors")
    if "priority_boosted_at" in columns:
        op.drop_column("tracked_actors", "priority_boosted_at")
