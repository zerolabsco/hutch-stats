"""tracked actor queue scheduling fields"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260411_0006"
down_revision = "20260411_0005"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("tracked_actors")

    if "discovery_state" not in columns:
        op.add_column(
            "tracked_actors",
            sa.Column("discovery_state", sa.String(length=32), nullable=False, server_default="queued"),
        )
    if "queued_for_discovery_at" not in columns:
        op.add_column("tracked_actors", sa.Column("queued_for_discovery_at", sa.DateTime(timezone=True), nullable=True))
    if "next_poll_after" not in columns:
        op.add_column("tracked_actors", sa.Column("next_poll_after", sa.DateTime(timezone=True), nullable=True))
    if "last_claimed_at" not in columns:
        op.add_column("tracked_actors", sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True))
    if "poll_attempts" not in columns:
        op.add_column(
            "tracked_actors",
            sa.Column("poll_attempts", sa.Integer(), nullable=False, server_default="0"),
        )

    op.execute(
        sa.text(
            """
            UPDATE tracked_actors
            SET discovery_state = CASE
                WHEN last_poll_status IS NOT NULL THEN last_poll_status
                ELSE 'queued'
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tracked_actors
            SET queued_for_discovery_at = COALESCE(queued_for_discovery_at, last_requested_at, last_polled_at, CURRENT_TIMESTAMP)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tracked_actors
            SET next_poll_after = COALESCE(next_poll_after, last_polled_at, CURRENT_TIMESTAMP)
            """
        )
    )


def downgrade() -> None:
    columns = _column_names("tracked_actors")
    if "poll_attempts" in columns:
        op.drop_column("tracked_actors", "poll_attempts")
    if "last_claimed_at" in columns:
        op.drop_column("tracked_actors", "last_claimed_at")
    if "next_poll_after" in columns:
        op.drop_column("tracked_actors", "next_poll_after")
    if "queued_for_discovery_at" in columns:
        op.drop_column("tracked_actors", "queued_for_discovery_at")
    if "discovery_state" in columns:
        op.drop_column("tracked_actors", "discovery_state")
