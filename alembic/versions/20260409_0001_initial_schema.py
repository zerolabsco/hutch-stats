"""initial schema"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from srht_contrib.config import Settings


revision = "20260409_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table_name)}


def _tracked_repositories_needs_upgrade() -> bool:
    inspector = inspect(op.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns("tracked_repositories")}
    actor_column = columns.get("actor")
    if actor_column is None or actor_column.get("nullable", True):
        return True

    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("tracked_repositories")}
    return "uq_tracked_repository_service_actor_name" not in unique_constraints


def _upgrade_tracked_repositories() -> None:
    default_actor = Settings().default_actor
    op.execute(
        sa.text(
            """
            CREATE TABLE tracked_repositories__alembic_new (
                id INTEGER NOT NULL PRIMARY KEY,
                service VARCHAR(32) NOT NULL,
                repo_name VARCHAR(255) NOT NULL,
                actor VARCHAR(255) NOT NULL,
                CONSTRAINT uq_tracked_repository_service_actor_name UNIQUE (service, actor, repo_name)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO tracked_repositories__alembic_new (id, service, repo_name, actor)
            SELECT id, service, repo_name, COALESCE(actor, :default_actor)
            FROM tracked_repositories
            """
        ).bindparams(default_actor=default_actor)
    )
    op.execute(sa.text("DROP TABLE tracked_repositories"))
    op.execute(sa.text("ALTER TABLE tracked_repositories__alembic_new RENAME TO tracked_repositories"))


def upgrade() -> None:
    table_names = _table_names()

    if "contribution_events" not in table_names:
        op.create_table(
            "contribution_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("service", sa.String(length=32), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("repo_name", sa.String(length=255), nullable=True),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("external_uid", sa.String(length=255), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("raw_payload_json", sa.JSON(), nullable=True),
            sa.UniqueConstraint("service", "external_uid", name="uq_contribution_event_service_uid"),
        )

    contribution_event_indexes = _index_names("contribution_events")
    if "ix_contribution_events_actor_occurred_at" not in contribution_event_indexes:
        op.create_index(
            "ix_contribution_events_actor_occurred_at",
            "contribution_events",
            ["actor", "occurred_at"],
            unique=False,
        )
    if "ix_contribution_events_service_occurred_at" not in contribution_event_indexes:
        op.create_index(
            "ix_contribution_events_service_occurred_at",
            "contribution_events",
            ["service", "occurred_at"],
            unique=False,
        )

    if "sync_states" not in table_names:
        op.create_table(
            "sync_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("service", sa.String(length=32), nullable=False),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("cursor_value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("service", "actor", name="uq_sync_state_service_actor"),
        )

    if "tracked_repositories" not in table_names:
        op.create_table(
            "tracked_repositories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("service", sa.String(length=32), nullable=False),
            sa.Column("repo_name", sa.String(length=255), nullable=False),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.UniqueConstraint("service", "actor", "repo_name", name="uq_tracked_repository_service_actor_name"),
        )
    elif _tracked_repositories_needs_upgrade():
        _upgrade_tracked_repositories()

    if "actor_aliases" not in table_names:
        op.create_table(
            "actor_aliases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("canonical_actor", sa.String(length=255), nullable=False),
            sa.Column("alias", sa.String(length=255), nullable=False),
            sa.UniqueConstraint("alias", name="uq_actor_alias_alias"),
        )


def downgrade() -> None:
    table_names = _table_names()

    if "actor_aliases" in table_names:
        op.drop_table("actor_aliases")
    if "tracked_repositories" in table_names:
        op.drop_table("tracked_repositories")
    if "sync_states" in table_names:
        op.drop_table("sync_states")
    if "contribution_events" in table_names:
        contribution_event_indexes = _index_names("contribution_events")
        if "ix_contribution_events_service_occurred_at" in contribution_event_indexes:
            op.drop_index("ix_contribution_events_service_occurred_at", table_name="contribution_events")
        if "ix_contribution_events_actor_occurred_at" in contribution_event_indexes:
            op.drop_index("ix_contribution_events_actor_occurred_at", table_name="contribution_events")
        op.drop_table("contribution_events")
