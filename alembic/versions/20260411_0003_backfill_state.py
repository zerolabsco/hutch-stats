"""actor and service backfill state"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260411_0003"
down_revision = "20260411_0002"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "tracked_actors" in _table_names():
        columns = _column_names("tracked_actors")
        if "backfill_status" not in columns:
            op.add_column("tracked_actors", sa.Column("backfill_status", sa.String(length=32), nullable=True))
            op.execute(sa.text("UPDATE tracked_actors SET backfill_status = 'pending' WHERE backfill_status IS NULL"))
        if "backfill_started_at" not in columns:
            op.add_column("tracked_actors", sa.Column("backfill_started_at", sa.DateTime(timezone=True), nullable=True))
        if "backfill_completed_at" not in columns:
            op.add_column("tracked_actors", sa.Column("backfill_completed_at", sa.DateTime(timezone=True), nullable=True))
        if "last_backfill_error" not in columns:
            op.add_column("tracked_actors", sa.Column("last_backfill_error", sa.Text(), nullable=True))

    if "service_backfill_states" not in _table_names():
        op.create_table(
            "service_backfill_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("service", sa.String(length=32), nullable=False),
            sa.Column("cursor_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("actor", "service", name="uq_service_backfill_state_actor_service"),
        )


def downgrade() -> None:
    if "service_backfill_states" in _table_names():
        op.drop_table("service_backfill_states")
