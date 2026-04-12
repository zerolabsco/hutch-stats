from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from srht_contrib.config import Settings
from srht_contrib.jobs.poller import PollerService
from srht_contrib.models import ContributionEvent, ServiceBackfillState, SyncState, TrackedActor, TrackedRepository
from srht_contrib.scripts.enqueue_actors import enqueue_actors
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.git import GitIngestionService, GitPollResult
from srht_contrib.services.todo import TodoIngestionService, TodoPollResult
from srht_contrib.services.types import BackfillBatchResult


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

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        return BackfillBatchResult(events=[], cursor_state=None, complete=True)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return BackfillBatchResult(events=[], cursor_state=None, complete=True)


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

    def fetch_recent_events(self, actor: str, since: datetime | None = None, repositories=None, db=None) -> GitPollResult:
        return GitPollResult(events=[], cursor="2026-03-31T00:00:00+00:00")

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        return BackfillBatchResult(events=[], cursor_state=None, complete=True)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return BackfillBatchResult(events=[], cursor_state=None, complete=True)


class BackfillingTodoService:
    service_name = "todo"

    def fetch_recent_events(self, actor: str, since: datetime | None = None) -> TodoPollResult:
        return TodoPollResult(events=[], cursor="2026-03-31T00:00:00+00:00")

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        event = NormalizedEvent(
            service="todo",
            event_type="ticket_created",
            actor=actor,
            repo_name="todo",
            resource_id="backfill-ticket",
            external_uid=f"todo:backfill:{actor}",
            occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            weight=1.0,
            raw_payload_json=None,
        )
        return BackfillBatchResult(events=[event], cursor_state=None, complete=True)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return self.fetch_backfill_batch(actor, cursor_state)


class QueueShrinkingTodoService:
    service_name = "todo"

    def fetch_recent_events(self, actor: str, since: datetime | None = None) -> TodoPollResult:
        return TodoPollResult(events=[], cursor=datetime(2026, 3, 31, tzinfo=UTC).isoformat())

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        import copy

        state = {
            "tracker_queue": ["t1", "t2", "t3", "t4", "t5", "t6"],
            "current_tracker": None,
            "current_ticket": None,
            "trackers_loaded": True,
            "trackers_cursor": None,
        }
        if cursor_state:
            state.update(copy.deepcopy(cursor_state))
        if not state["tracker_queue"]:
            return BackfillBatchResult(events=[], cursor_state=None, complete=True)
        state["tracker_queue"].pop(0)
        return BackfillBatchResult(events=[], cursor_state=state, complete=False)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return self.fetch_backfill_batch(actor, cursor_state)


class QueueShrinkingGitService:
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

    def fetch_recent_events(self, actor: str, since: datetime | None = None, repositories=None, db=None) -> GitPollResult:
        return GitPollResult(events=[], cursor=datetime(2026, 3, 31, tzinfo=UTC).isoformat())

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        import copy

        state = {
            "repository_queue": ["r1", "r2", "r3", "r4", "r5", "r6"],
            "current_repository": None,
            "discovery_complete": True,
            "discovery_cursor": None,
        }
        if cursor_state:
            state.update(copy.deepcopy(cursor_state))
        if not state["repository_queue"]:
            return BackfillBatchResult(events=[], cursor_state=None, complete=True)
        state["repository_queue"].pop(0)
        return BackfillBatchResult(events=[], cursor_state=state, complete=False)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return self.fetch_backfill_batch(actor, cursor_state)


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
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

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
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

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
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

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
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

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
                            {
                                "name": "Hutch",
                                "visibility": "PUBLIC",
                                "owner": {"canonicalName": "~ccleberg"},
                            },
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
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

    inserted = poller.poll_all(db_session, "~ccleberg")

    assert inserted == 1
    assert any("query UserRepositories" in call[0] for call in client.calls)


def test_sync_overlap_reuses_cursor_window_and_suppresses_duplicates(db_session) -> None:
    settings = make_settings()
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
    poller = PollerService(todo_service=todo_service, git_service=EmptyGitService(), settings=settings)

    first_inserted = poller.poll_all(db_session, "~ccleberg")
    second_inserted = poller.poll_all(db_session, "~ccleberg")

    state = db_session.scalar(select(SyncState).where(SyncState.service == "todo").where(SyncState.actor == "~ccleberg"))

    assert first_inserted == 1
    assert second_inserted == 0
    assert state is not None
    assert len(todo_service.calls) == 2
    assert todo_service.calls[1].isoformat() == "2026-03-30T23:00:00+00:00"


def test_scheduled_poll_polls_known_actors_and_seeds_default_actor(db_session) -> None:
    settings = make_settings()
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
    poller = PollerService(todo_service=todo_service, git_service=EmptyGitService(), settings=settings)

    db_session.add(TrackedActor(actor="~known", is_active=True))
    db_session.commit()

    results = poller.poll_tracked_actors(db_session, default_actor="~default")

    tracked_actors = db_session.scalars(select(TrackedActor).order_by(TrackedActor.actor)).all()

    assert results == {"~default": 1, "~known": 0}
    assert [actor.actor for actor in tracked_actors] == ["~default", "~known"]
    assert all(actor.last_poll_status == "indexed" for actor in tracked_actors)
    assert all(actor.last_polled_at is not None for actor in tracked_actors)


def test_poll_marks_backfill_complete_and_persists_service_state(db_session) -> None:
    settings = make_settings()
    poller = PollerService(todo_service=BackfillingTodoService(), git_service=EmptyGitService(), settings=settings)

    inserted = poller.poll_all(db_session, "~ccleberg")

    tracked_actor = db_session.scalar(select(TrackedActor).where(TrackedActor.actor == "~ccleberg"))
    service_states = db_session.scalars(
        select(ServiceBackfillState)
        .where(ServiceBackfillState.actor == "~ccleberg")
        .order_by(ServiceBackfillState.scope, ServiceBackfillState.service)
    ).all()

    assert inserted == 1
    assert tracked_actor is not None
    assert tracked_actor.recent_backfill_status == "completed"
    assert tracked_actor.recent_backfill_completed_at is not None
    assert [f"{state.scope}:{state.service}" for state in service_states] == ["recent:git", "recent:todo"]
    assert all(state.status == "completed" for state in service_states)


def test_backfill_cursor_state_shrinks_across_repeated_polls(db_session) -> None:
    settings = make_settings()
    poller = PollerService(
        todo_service=QueueShrinkingTodoService(),
        git_service=QueueShrinkingGitService(),
        settings=settings,
    )

    poller.poll_all(db_session, "~ccleberg")
    first_states = {
        state.service: state.cursor_json
        for state in db_session.scalars(
            select(ServiceBackfillState)
            .where(ServiceBackfillState.actor == "~ccleberg")
            .where(ServiceBackfillState.scope == "recent")
        ).all()
    }

    poller.poll_all(db_session, "~ccleberg")
    second_states = {
        state.service: state.cursor_json
        for state in db_session.scalars(
            select(ServiceBackfillState)
            .where(ServiceBackfillState.actor == "~ccleberg")
            .where(ServiceBackfillState.scope == "recent")
        ).all()
    }

    assert first_states["git"]["repository_queue"] == ["r6"]
    assert first_states["todo"]["tracker_queue"] == ["t6"]
    assert second_states["git"] is None
    assert second_states["todo"] is None


def test_prune_old_events_removes_data_older_than_one_year(db_session) -> None:
    settings = make_settings()
    poller = PollerService(todo_service=BackfillingTodoService(), git_service=EmptyGitService(), settings=settings)
    db_session.add_all(
        [
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor="~ccleberg",
                repo_name="todo",
                resource_id="old",
                external_uid="todo:old",
                occurred_at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            ),
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor="~ccleberg",
                repo_name="todo",
                resource_id="recent",
                external_uid="todo:recent",
                occurred_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            ),
        ]
    )
    db_session.commit()

    deleted = poller.prune_old_events(db_session)
    remaining = db_session.scalars(
        select(ContributionEvent.external_uid).order_by(ContributionEvent.external_uid)
    ).all()

    assert deleted == 1
    assert remaining == ["todo:recent"]


def test_poll_tracked_actors_limits_to_due_batch_size(db_session) -> None:
    settings = make_settings(DISCOVERY_BATCH_SIZE=2, INDEXED_ACTOR_REPOLL_SECONDS=3600)
    todo_service = RecordingTodoService(events_by_call=[[], []])
    git_service = EmptyGitService()
    poller = PollerService(todo_service=todo_service, git_service=git_service, settings=settings)

    now = datetime.now(tz=UTC)
    db_session.add_all(
        [
            TrackedActor(
                actor="~a",
                is_active=True,
                discovery_state="queued",
                queued_for_discovery_at=now - timedelta(minutes=3),
                next_poll_after=now - timedelta(minutes=3),
                recent_backfill_status="completed",
            ),
            TrackedActor(
                actor="~b",
                is_active=True,
                discovery_state="queued",
                queued_for_discovery_at=now - timedelta(minutes=2),
                next_poll_after=now - timedelta(minutes=2),
                recent_backfill_status="completed",
            ),
            TrackedActor(
                actor="~c",
                is_active=True,
                discovery_state="queued",
                queued_for_discovery_at=now - timedelta(minutes=1),
                next_poll_after=now - timedelta(minutes=1),
                recent_backfill_status="completed",
            ),
        ]
    )
    db_session.commit()

    results = poller.poll_tracked_actors(db_session)

    assert set(results) == {"~a", "~b"}
    actors = {
        actor.actor: actor
        for actor in db_session.scalars(select(TrackedActor).order_by(TrackedActor.actor)).all()
    }
    assert actors["~a"].discovery_state == "indexed"
    assert actors["~b"].discovery_state == "indexed"
    assert actors["~c"].discovery_state == "queued"
    assert actors["~a"].poll_attempts == 1
    assert actors["~b"].poll_attempts == 1
    assert actors["~c"].poll_attempts == 0


def test_enqueue_actors_staggers_without_polling(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "enqueue.db"
    username_path = tmp_path / "srht_usernames.txt"
    username_path.write_text("alice\nbob\nalice\n~carol\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("SRHT_TOKEN", "test-token")
    monkeypatch.setenv("DEFAULT_ACTOR", "~ccleberg")

    from srht_contrib.db import Base, make_engine, make_session_factory

    settings = Settings()
    engine = make_engine(settings)
    Base.metadata.create_all(bind=engine)
    session_factory = make_session_factory(settings)
    queued_at = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)

    inserted = enqueue_actors(Path(username_path), stagger_seconds=60, start_at=queued_at)

    with session_factory() as db:
        actors = db.scalars(select(TrackedActor).order_by(TrackedActor.actor)).all()

    assert inserted == 3
    assert [actor.actor for actor in actors] == ["~alice", "~bob", "~carol"]
    assert all(actor.discovery_state == "queued" for actor in actors)
    assert actors[0].next_poll_after == queued_at.replace(tzinfo=None)
    assert actors[1].next_poll_after == (queued_at + timedelta(seconds=60)).replace(tzinfo=None)
    assert actors[2].next_poll_after == (queued_at + timedelta(seconds=120)).replace(tzinfo=None)


def test_enqueue_actors_skips_invalid_usernames(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "enqueue-invalid.db"
    username_path = tmp_path / "srht_usernames.txt"
    username_path.write_text("-0\n.\n~bad-\nvalid_user\nok.ok\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("SRHT_TOKEN", "test-token")
    monkeypatch.setenv("DEFAULT_ACTOR", "~ccleberg")

    from srht_contrib.db import Base, make_engine, make_session_factory

    settings = Settings()
    engine = make_engine(settings)
    Base.metadata.create_all(bind=engine)
    session_factory = make_session_factory(settings)

    inserted = enqueue_actors(Path(username_path), stagger_seconds=60)

    with session_factory() as db:
        actors = db.scalars(select(TrackedActor).order_by(TrackedActor.actor)).all()

    assert inserted == 2
    assert [actor.actor for actor in actors] == ["~ok.ok", "~valid_user"]
