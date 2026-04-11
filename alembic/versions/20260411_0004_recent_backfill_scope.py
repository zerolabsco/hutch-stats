"""recent backfill scope and actor fields"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260411_0004"
down_revision = "20260411_0003"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _service_backfill_needs_upgrade() -> bool:
    if "service_backfill_states" not in _table_names():
        return False
    columns = _column_names("service_backfill_states")
    if "scope" not in columns:
        return True
    unique_constraints = {
        constraint["name"]
        for constraint in inspect(op.get_bind()).get_unique_constraints("service_backfill_states")
    }
    return "uq_service_backfill_state_actor_service_scope" not in unique_constraints


def _upgrade_service_backfill_states() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE service_backfill_states__alembic_new (
                id INTEGER NOT NULL PRIMARY KEY,
                actor VARCHAR(255) NOT NULL,
                service VARCHAR(32) NOT NULL,
                scope VARCHAR(16) NOT NULL,
                cursor_json JSON,
                status VARCHAR(32) NOT NULL,
                started_at DATETIME,
                completed_at DATETIME,
                last_error TEXT,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_service_backfill_state_actor_service_scope UNIQUE (actor, service, scope)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO service_backfill_states__alembic_new
                (id, actor, service, scope, cursor_json, status, started_at, completed_at, last_error, updated_at)
            SELECT id, actor, service, 'full', cursor_json, status, started_at, completed_at, last_error, updated_at
            FROM service_backfill_states
            """
        )
    )
    op.execute(sa.text("DROP TABLE service_backfill_states"))
    op.execute(sa.text("ALTER TABLE service_backfill_states__alembic_new RENAME TO service_backfill_states"))


def upgrade() -> None:
    if "tracked_actors" in _table_names():
        columns = _column_names("tracked_actors")
        if "recent_backfill_status" not in columns:
            op.add_column("tracked_actors", sa.Column("recent_backfill_status", sa.String(length=32), nullable=True))
            op.execute(sa.text("UPDATE tracked_actors SET recent_backfill_status = 'pending' WHERE recent_backfill_status IS NULL"))
        if "recent_backfill_started_at" not in columns:
            op.add_column("tracked_actors", sa.Column("recent_backfill_started_at", sa.DateTime(timezone=True), nullable=True))
        if "recent_backfill_completed_at" not in columns:
            op.add_column("tracked_actors", sa.Column("recent_backfill_completed_at", sa.DateTime(timezone=True), nullable=True))
        if "last_recent_backfill_error" not in columns:
            op.add_column("tracked_actors", sa.Column("last_recent_backfill_error", sa.Text(), nullable=True))

    if _service_backfill_needs_upgrade():
        _upgrade_service_backfill_states()


def downgrade() -> None:
    pass
