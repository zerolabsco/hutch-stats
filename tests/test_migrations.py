from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from srht_contrib.config import Settings


def test_alembic_upgrade_creates_schema(tmp_path) -> None:
    database_path = tmp_path / "migrated.db"
    database_url = f"sqlite:///{database_path}"
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert sorted(inspector.get_table_names()) == [
        "actor_aliases",
        "alembic_version",
        "contribution_events",
        "discovered_repositories",
        "service_backfill_states",
        "sync_states",
        "tracked_actors",
        "tracked_repositories",
    ]


def test_alembic_upgrade_adopts_legacy_schema(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE contribution_events (
                    id INTEGER NOT NULL PRIMARY KEY,
                    service VARCHAR(32) NOT NULL,
                    event_type VARCHAR(64) NOT NULL,
                    actor VARCHAR(255) NOT NULL,
                    repo_name VARCHAR(255),
                    resource_id VARCHAR(255) NOT NULL,
                    external_uid VARCHAR(255) NOT NULL,
                    occurred_at DATETIME NOT NULL,
                    weight FLOAT NOT NULL,
                    raw_payload_json JSON,
                    CONSTRAINT uq_contribution_event_service_uid UNIQUE (service, external_uid)
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX ix_contribution_events_actor_occurred_at ON contribution_events (actor, occurred_at)"))
        connection.execute(text("CREATE INDEX ix_contribution_events_service_occurred_at ON contribution_events (service, occurred_at)"))
        connection.execute(
            text(
                """
                CREATE TABLE sync_states (
                    id INTEGER NOT NULL PRIMARY KEY,
                    service VARCHAR(32) NOT NULL,
                    actor VARCHAR(255) NOT NULL,
                    cursor_value TEXT,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_sync_state_service_actor UNIQUE (service, actor)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE tracked_repositories (
                    id INTEGER NOT NULL PRIMARY KEY,
                    service VARCHAR(32) NOT NULL,
                    repo_name VARCHAR(255) NOT NULL,
                    actor VARCHAR(255),
                    CONSTRAINT uq_tracked_repository_service_name UNIQUE (service, repo_name)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO tracked_repositories (id, service, repo_name, actor)
                VALUES (1, 'git', 'Hutch', NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE actor_aliases (
                    id INTEGER NOT NULL PRIMARY KEY,
                    canonical_actor VARCHAR(255) NOT NULL,
                    alias VARCHAR(255) NOT NULL,
                    CONSTRAINT uq_actor_alias_alias UNIQUE (alias)
                )
                """
            )
        )

    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    columns = {column["name"]: column for column in inspector.get_columns("tracked_repositories")}
    tracked_actor_columns = {column["name"] for column in inspector.get_columns("tracked_actors")}
    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("tracked_repositories")}
    with create_engine(database_url).connect() as connection:
        actor = connection.execute(text("SELECT actor FROM tracked_repositories WHERE id = 1")).scalar_one()

    assert columns["actor"]["nullable"] is False
    assert "uq_tracked_repository_service_actor_name" in unique_constraints
    assert actor == Settings().default_actor
    assert "discovered_repositories" in inspector.get_table_names()
    assert "tracked_actors" in inspector.get_table_names()
    assert "service_backfill_states" in inspector.get_table_names()
    assert {
        "discovery_state",
        "queued_for_discovery_at",
        "priority_boosted_at",
        "next_poll_after",
        "last_claimed_at",
        "poll_attempts",
    } <= tracked_actor_columns


def test_alembic_prefers_database_url_from_environment(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "env-selected.db"
    database_url = f"sqlite:///{database_path}"
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert "actor_aliases" in inspector.get_table_names()
    assert "discovered_repositories" in inspector.get_table_names()
    assert "tracked_actors" in inspector.get_table_names()
    assert "service_backfill_states" in inspector.get_table_names()
