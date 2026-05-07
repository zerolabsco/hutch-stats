from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    app_name: str = "srht-contrib"
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    enable_scheduler: bool = Field(default=False, alias="ENABLE_SCHEDULER")
    api_key: str = Field(default="", alias="API_KEY")

    srht_token: str = Field(default="", alias="SRHT_TOKEN")
    todo_srht_endpoint: str = Field(
        default="https://todo.sr.ht/query",
        alias="TODO_SRHT_ENDPOINT",
    )
    git_srht_endpoint: str = Field(
        default="https://git.sr.ht/query",
        alias="GIT_SRHT_ENDPOINT",
    )
    database_url: str = Field(
        default="sqlite:///./srht_contrib.db",
        alias="DATABASE_URL",
    )
    default_actor: str = Field(default="~unknown", alias="DEFAULT_ACTOR")
    poll_interval_seconds: int = Field(default=300, alias="POLL_INTERVAL_SECONDS")
    sync_overlap_hours: int = Field(default=1, alias="SYNC_OVERLAP_HOURS")
    srht_request_delay_seconds: float = Field(default=0.5, alias="SRHT_REQUEST_DELAY_SECONDS")
    sqlite_busy_timeout_seconds: float = Field(default=30.0, alias="SQLITE_BUSY_TIMEOUT_SECONDS")
    discovery_batch_size: int = Field(default=20, alias="DISCOVERY_BATCH_SIZE")
    indexed_actor_repoll_seconds: int = Field(default=21600, alias="INDEXED_ACTOR_REPOLL_SECONDS")
    discovery_error_backoff_seconds: int = Field(default=3600, alias="DISCOVERY_ERROR_BACKOFF_SECONDS")
    discovery_error_backoff_max_seconds: int = Field(default=21600, alias="DISCOVERY_ERROR_BACKOFF_MAX_SECONDS")
    git_repo_discovery_ttl_seconds: int = Field(default=3600, alias="GIT_REPO_DISCOVERY_TTL_SECONDS")
    actor_aliases_json: dict[str, list[str]] = Field(
        default_factory=dict,
        alias="ACTOR_ALIASES_JSON",
    )
    git_tracked_repositories: list[str] = Field(
        default_factory=list,
        alias="GIT_TRACKED_REPOSITORIES",
    )

    event_weights: dict[str, float] = {
        "commit": 1.0,
        "ticket_created": 1.0,
        "ticket_comment": 0.5,
        "ticket_closed": 0.75,
        "build_started": 0.25,
        "build_passed": 0.25,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
