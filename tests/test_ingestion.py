from datetime import UTC, datetime

from sqlalchemy import select

from srht_contrib.config import Settings
from srht_contrib.jobs.poller import PollerService
from srht_contrib.models import SyncState, TrackedActor, TrackedRepository
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.git import GitIngestionService, GitPollResult
from srht_contrib.services.todo import TodoIngestionService, TodoPollResult


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


class RecordingTodoService:
    service_name = "todo"

    def __init__(self, events_by_call: list[list[NormalizedEvent]]) -> None:
        self.events_by_call = events_by_call
        self.calls: list[datetime] = []

    def fetch_recent_events(self, actor: str, since: datetime | None = None) -> TodoPollResult:
        assert since is not None
        self.calls.append(since)
        events = self.events_by_call.pop(0)
        return TodoPollResult(events=events, cursor="2026-03-31T00:00:00+00:00")


class EmptyGitService:
    service_name = "git"

    def __init__(self) -> None:
        self.settings = Settings(
            SRHT_TOKEN="x",
            DATABASE_URL="sqlite://",
            DEFAULT_ACTOR="~ccleberg",
            TODO_SRHT_ENDPOINT="https://todo.sr.ht/query",
            GIT_SRHT_ENDPOINT="https://git.sr.ht/query",
            POLL_INTERVAL_SECONDS=60,
        )

    def fetch_recent_events(self, actor: str, since: datetime | None = None, repositories=None) -> GitPollResult:
        return GitPollResult(events=[], cursor="2026-03-31T00:00:00+00:00")


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
    }
    values.update(overrides)
    return Settings(**values)


def test_todo_ingestion_is_idempotent(db_session) -> None:
    settings = make_settings()
    payload = {
        "me": {"canonicalName": "~ccleberg"},
        "events": {
            "results": [
                {
                    "id": "1001",
                    "created": "2026-03-29T10:00:00Z",
                    "ticket": {
                        "id": "123",
                        "ref": "~ccleberg/todo/123",
                        "status": "RESOLVED",
                        "resolution": "CLOSED",
                        "tracker": {"name": "todo"},
                    },
                    "changes": [
                        {
                            "__typename": "Created",
                            "eventType": "CREATED",
                            "ticket": {"id": "123"},
                            "author": {"canonicalName": "~ccleberg"},
                        }
                    ],
                },
                {
                    "id": "1002",
                    "created": "2026-03-30T09:00:00Z",
                    "ticket": {
                        "id": "123",
                        "ref": "~ccleberg/todo/123",
                        "status": "RESOLVED",
                        "resolution": "CLOSED",
                        "tracker": {"name": "todo"},
                    },
                    "changes": [
                        {
                            "__typename": "Comment",
                            "eventType": "COMMENT",
                            "ticket": {"id": "123"},
                            "author": {"canonicalName": "~ccleberg"},
                        }
                    ],
                },
                {
                    "id": "1003",
                    "created": "2026-03-30T10:00:00Z",
                    "ticket": {
                        "id": "123",
                        "ref": "~ccleberg/todo/123",
                        "status": "RESOLVED",
                        "resolution": "CLOSED",
                        "tracker": {"name": "todo"},
                    },
                    "changes": [
                        {
                            "__typename": "StatusChange",
                            "eventType": "STATUS_CHANGE",
                            "ticket": {"id": "123"},
                            "editor": {"canonicalName": "~ccleberg"},
                            "oldStatus": "IN_PROGRESS",
                            "newStatus": "RESOLVED",
                            "oldResolution": "UNRESOLVED",
                            "newResolution": "CLOSED",
                        }
                    ],
                },
            ],
            "cursor": None,
        },
    }

    todo_service = TodoIngestionService(StubClient(payload), settings)
    git_service = GitIngestionService(StubClient(payload={}), settings)
    poller = PollerService(todo_service=todo_service, git_service=git_service)

    first_inserted = poller.poll_all(db_session, "~ccleberg")
    second_inserted = poller.poll_all(db_session, "~ccleberg")

    assert first_inserted == 3
    assert second_inserted == 0


def test_todo_ingestion_falls_back_to_tracker_crawl(db_session) -> None:
    settings = make_settings()
    client = StubClient(
        payloads_by_query={
            "query TodoActivity": {
                "me": {"canonicalName": "~ccleberg"},
                "events": {"results": [], "cursor": None},
            },
            "query TodoTrackers": {
                "me": {
                    "canonicalName": "~ccleberg",
                    "trackers": {"results": [{"id": "1", "rid": "tracker-rid", "name": "todo"}], "cursor": None},
                }
            },
            "query TodoTrackerTickets": {
                "tracker": {
                    "id": "1",
                    "name": "todo",
                    "tickets": {
                        "results": [
                            {
                                "id": 123,
                                "ref": "~ccleberg/todo/123",
                                "created": "2026-03-29T09:00:00Z",
                                "updated": "2026-03-30T09:00:00Z",
                                "status": "RESOLVED",
                                "resolution": "CLOSED",
                                "submitter": {"canonicalName": "~ccleberg"},
                            }
                        ],
                        "cursor": None,
                    },
                }
            },
            "query TodoTicketEvents": {
                "tracker": {
                    "ticket": {
                        "id": 123,
                        "ref": "~ccleberg/todo/123",
                        "status": "RESOLVED",
                        "resolution": "CLOSED",
                        "events": {
                            "results": [
                                {
                                    "id": "evt-1",
                                    "created": "2026-03-30T09:00:00Z",
                                    "changes": [
                                        {
                                            "__typename": "Comment",
                                            "eventType": "COMMENT",
                                            "ticket": {"id": "123"},
                                            "author": {"canonicalName": "~ccleberg"},
                                        }
                                    ],
                                }
                            ],
                            "cursor": None,
                        },
                    }
                }
            },
        }
    )
    todo_service = TodoIngestionService(client, settings)
    git_service = GitIngestionService(StubClient(payload={}), settings)
    poller = PollerService(todo_service=todo_service, git_service=git_service)

    inserted = poller.poll_all(db_session, "~ccleberg")

    assert inserted == 1
    assert any("query TodoTrackers" in call[0] for call in client.calls)


def test_unsupported_todo_changes_are_ignored(db_session) -> None:
    settings = make_settings()
    payload = {
        "me": {"canonicalName": "~ccleberg"},
        "events": {
            "results": [
                {
                    "id": "1001",
                    "created": "2026-03-29T10:00:00Z",
                    "ticket": {
                        "id": "123",
                        "ref": "~ccleberg/todo/123",
                        "status": "OPEN",
                        "resolution": "UNRESOLVED",
                        "tracker": {"name": "todo"},
                    },
                    "changes": [
                        {"__typename": "LabelUpdate", "eventType": "LABEL_UPDATE", "ticket": {"id": "123"}},
                        {"__typename": "TicketMention", "eventType": "TICKET_MENTION", "ticket": {"id": "123"}},
                    ],
                }
            ],
            "cursor": None,
        },
    }

    todo_service = TodoIngestionService(StubClient(payload), settings)
    git_service = GitIngestionService(StubClient(payload={}), settings)
    poller = PollerService(todo_service=todo_service, git_service=git_service)

    inserted = poller.poll_all(db_session, "~ccleberg")

    assert inserted == 0


def test_git_ingestion_normalizes_commit_aliases_and_repository_names(db_session) -> None:
    settings = make_settings(
        ACTOR_ALIASES_JSON={"~ccleberg": ["cmc@example.com", "Chris Cleberg"]},
        GIT_TRACKED_REPOSITORIES=["Hutch"],
    )
    git_payload = {
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
                            "message": "Add contribution calendar",
                        }
                    ],
                    "cursor": None,
                },
            }
        }
    }

    todo_service = TodoIngestionService(
        StubClient(payload={"me": {"canonicalName": "~ccleberg"}, "events": {"results": [], "cursor": None}}),
        settings,
    )
    git_service = GitIngestionService(StubClient(payloads_by_query={"query RepositoryLog": git_payload}), settings)
    poller = PollerService(todo_service=todo_service, git_service=git_service)

    inserted = poller.poll_all(db_session, "~ccleberg")

    assert inserted == 1

    tracked_repositories = db_session.scalars(select(TrackedRepository.repo_name)).all()
    assert tracked_repositories == ["~ccleberg/Hutch"]


def test_git_ingestion_auto_discovers_owned_repositories(db_session) -> None:
    settings = make_settings(
        ACTOR_ALIASES_JSON={"~ccleberg": ["cmc@example.com", "Chris Cleberg"]},
        GIT_TRACKED_REPOSITORIES=[],
    )
    client = StubClient(
        payloads_by_query={
            "query UserRepositories": {
                "user": {
                    "repositories": {
                        "results": [
                            {"name": "Hutch", "owner": {"canonicalName": "~ccleberg"}},
                        ],
                        "cursor": None,
                    }
                }
            },
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
    poller = PollerService(todo_service=todo_service, git_service=git_service)

    inserted = poller.poll_all(db_session, "~ccleberg")

    assert inserted == 1
    assert any("query UserRepositories" in call[0] for call in client.calls)


def test_sync_overlap_reuses_cursor_window_and_suppresses_duplicates(db_session) -> None:
    event = NormalizedEvent(
        service="todo",
        event_type="ticket_created",
        actor="~ccleberg",
        repo_name="todo",
        resource_id="123",
        external_uid="todo:event:123:created:123",
        occurred_at=datetime(2026, 3, 30, 10, 0, tzinfo=UTC),
        weight=1.0,
        raw_payload_json=None,
    )
    todo_service = RecordingTodoService(events_by_call=[[event], [event]])
    poller = PollerService(todo_service=todo_service, git_service=EmptyGitService())

    first_inserted = poller.poll_all(db_session, "~ccleberg")
    second_inserted = poller.poll_all(db_session, "~ccleberg")

    state = db_session.scalar(select(SyncState).where(SyncState.service == "todo").where(SyncState.actor == "~ccleberg"))

    assert first_inserted == 1
    assert second_inserted == 0
    assert state is not None
    assert len(todo_service.calls) == 2
    assert todo_service.calls[1].isoformat() == "2026-03-30T00:00:00+00:00"


def test_scheduled_poll_polls_known_actors_and_seeds_default_actor(db_session) -> None:
    event = NormalizedEvent(
        service="todo",
        event_type="ticket_created",
        actor="~known",
        repo_name="todo",
        resource_id="123",
        external_uid="todo:event:known:created:123",
        occurred_at=datetime(2026, 3, 30, 10, 0, tzinfo=UTC),
        weight=1.0,
        raw_payload_json=None,
    )
    todo_service = RecordingTodoService(events_by_call=[[], [event]])
    poller = PollerService(todo_service=todo_service, git_service=EmptyGitService())

    db_session.add(TrackedActor(actor="~known", is_active=True))
    db_session.commit()

    results = poller.poll_tracked_actors(db_session, default_actor="~default")

    tracked_actors = db_session.scalars(select(TrackedActor).order_by(TrackedActor.actor)).all()

    assert results == {"~default": 0, "~known": 1}
    assert [actor.actor for actor in tracked_actors] == ["~default", "~known"]
    assert all(actor.last_poll_status == "indexed" for actor in tracked_actors)
    assert all(actor.last_polled_at is not None for actor in tracked_actors)
