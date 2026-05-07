from datetime import UTC, datetime

from sqlalchemy import select

from srht_contrib.config import Settings
from srht_contrib.jobs.poller import PollerService
from srht_contrib.models import DiscoveredRepository
from srht_contrib.services.git import GitIngestionService
from srht_contrib.services.todo import TodoIngestionService


class StubClient:
    def __init__(self, payload: dict | None = None, payloads_by_query: dict[str, dict] | None = None) -> None:
        self.payload = payload or {}
        self.payloads_by_query = payloads_by_query or {}
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        for marker, payload in self.payloads_by_query.items():
            if marker in query:
                return payload
        return self.payload


def make_settings(**overrides) -> Settings:
    values = {
        "API_KEY": "test-api-key",
        "ENABLE_SCHEDULER": False,
        "SRHT_TOKEN": "x",
        "DATABASE_URL": "sqlite://",
        "DEFAULT_ACTOR": "~ccleberg",
        "TODO_SRHT_ENDPOINT": "https://todo.sr.ht/query",
        "GIT_SRHT_ENDPOINT": "https://git.sr.ht/query",
        "POLL_INTERVAL_SECONDS": 60,
        "GIT_TRACKED_REPOSITORIES": [],
        "ACTOR_ALIASES_JSON": {"~ccleberg": ["cmc@example.com", "Chris Cleberg"]},
    }
    values.update(overrides)
    return Settings(**values)


def branch_payload(*branches: str) -> dict:
    return {
        "user": {
            "repository": {
                "references": {
                    "results": [{"name": branch, "target": "abc123"} for branch in branches],
                    "cursor": None,
                }
            }
        }
    }


def test_git_poll_reuses_cached_discovered_repositories(db_session) -> None:
    settings = make_settings()
    client = StubClient(
        payloads_by_query={
            "query UserRepositories": {
                "user": {
                    "repositories": {
                        "results": [
                            {
                                "name": "Hutch",
                                "visibility": "PUBLIC",
                                "owner": {"canonicalName": "~ccleberg"},
                            }
                        ],
                        "cursor": None,
                    }
                }
            },
            "query RepositoryBranches": branch_payload("refs/heads/main"),
            "query RepositoryLog": {
                "user": {
                    "repository": {
                        "name": "Hutch",
                        "owner": {"canonicalName": "~ccleberg"},
                        "log": {
                            "results": [
                                {
                                    "id": "abc123",
                                    "shortId": "abc123",
                                    "author": {
                                        "name": "Chris Cleberg",
                                        "email": "cmc@example.com",
                                        "time": "2026-03-30T12:00:00Z",
                                    },
                                    "committer": {
                                        "name": "Chris Cleberg",
                                        "email": "cmc@example.com",
                                        "time": "2026-03-30T12:00:00Z",
                                    },
                                    "message": "Auto-discovered repo commit",
                                }
                            ],
                            "cursor": None,
                        },
                    }
                }
            },
        }
    )
    todo_service = TodoIngestionService(
        StubClient(payload={"me": {"canonicalName": "~ccleberg"}, "events": {"results": [], "cursor": None}}),
        settings,
    )
    git_service = GitIngestionService(client, settings)
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

    first_inserted = poller.poll_all(db_session, "~ccleberg")
    first_poll_user_repository_calls = [call for call in client.calls if "query UserRepositories" in call[0]]
    second_inserted = poller.poll_all(db_session, "~ccleberg")

    user_repository_calls = [call for call in client.calls if "query UserRepositories" in call[0]]
    cached_names = db_session.scalars(select(DiscoveredRepository.name)).all()

    assert first_inserted == 1
    assert second_inserted == 0
    assert len(user_repository_calls) == len(first_poll_user_repository_calls)
    assert cached_names == ["~ccleberg/Hutch"]
