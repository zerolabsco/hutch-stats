"""tracked actors for lazy indexing"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260411_0002"
down_revision = "20260409_0001"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "tracked_actors" in _table_names():
        return

    op.create_table(
        "tracked_actors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_status", sa.String(length=32), nullable=True),
        sa.Column("last_poll_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("actor", name="uq_tracked_actor_actor"),
    )


def downgrade() -> None:
    if "tracked_actors" in _table_names():
        op.drop_table("tracked_actors")
