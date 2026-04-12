"""discovered repository cache"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260411_0005"
down_revision = "20260411_0004"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "discovered_repositories" in _table_names():
        return

    op.create_table(
        "discovered_repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor", "name", name="uq_discovered_repository_actor_name"),
    )


def downgrade() -> None:
    if "discovered_repositories" in _table_names():
        op.drop_table("discovered_repositories")
