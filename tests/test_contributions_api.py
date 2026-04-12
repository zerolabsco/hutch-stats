from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from srht_contrib.main import create_app
from srht_contrib.models import ContributionEvent, TrackedActor


class _Closable:
    def close(self) -> None:
        return None


def test_read_only_contribution_routes_are_public_and_write_routes_require_api_key(settings, db_engine, session_factory) -> None:
    app = create_app(settings, engine=db_engine, session_factory=session_factory)
    with TestClient(app) as open_client:
        response = open_client.get("/health")
        public_contributions = open_client.get("/api/contributions/~ccleberg?from=2026-03-28&to=2026-03-30")
        public_stats = open_client.get("/api/contributions/~ccleberg/stats?from=2026-03-28&to=2026-03-30")
    assert response.status_code == 200
    assert public_contributions.status_code == 200
    assert public_stats.status_code == 200

    with TestClient(app) as unauthorized:
        unauthorized_response = unauthorized.post("/api/contributions/poll?actor=~ccleberg")
    assert unauthorized_response.status_code == 401

    with TestClient(app) as invalid:
        invalid.headers.update({"X-API-Key": "wrong-key"})
        invalid_response = invalid.get("/api/repositories")
    assert invalid_response.status_code == 401


def test_contributions_api_returns_zero_filled_range(client: TestClient, db_session) -> None:
    db_session.add(
        ContributionEvent(
            service="todo",
            event_type="ticket_created",
            actor="~ccleberg",
            repo_name=None,
            resource_id="1",
            external_uid="todo:ticket:1:created",
            occurred_at=datetime(2026, 3, 30, 10, 0, tzinfo=UTC),
            weight=1.0,
            raw_payload_json=None,
        )
    )
    db_session.commit()

    response = client.get("/api/contributions/~ccleberg?from=2026-03-28&to=2026-03-30")

    assert response.status_code == 200
    assert response.json()["is_indexed"] is True
    assert response.json()["indexing_state"] == "indexed"
    assert response.json()["is_recent_window_backfilled"] is False
    assert response.json()["recent_backfill_state"] == "pending"
    assert response.json()["days"] == [
        {"date": "2026-03-28", "count": 0, "score": 0.0},
        {"date": "2026-03-29", "count": 0, "score": 0.0},
        {"date": "2026-03-30", "count": 1, "score": 1.0},
    ]


def test_public_read_registers_actor_for_lazy_indexing(client: TestClient, db_session) -> None:
    response = client.get("/api/contributions/~ccleberg?from=2026-03-28&to=2026-03-30")

    tracked_actor = db_session.scalar(select(TrackedActor).where(TrackedActor.actor == "~ccleberg"))

    assert response.status_code == 200
    assert response.json()["is_indexed"] is False
    assert response.json()["indexing_state"] == "pending"
    assert response.json()["is_recent_window_backfilled"] is False
    assert response.json()["recent_backfill_state"] == "pending"
    assert response.json()["recent_backfill_completed_at"] is None
    assert response.json()["last_polled_at"] is None
    assert tracked_actor is not None
    assert tracked_actor.is_active is True
    assert tracked_actor.last_requested_at is not None
    assert tracked_actor.priority_boosted_at is None


class RecordingPriorityPoller:
    def __init__(self) -> None:
        service = type("Service", (), {"client": _Closable()})()
        self.todo_service = service
        self.git_service = service
        self.calls: list[tuple[str, bool]] = []

    def track_actor_request(self, db, actor: str, *, update_last_requested: bool = True, prioritize: bool = False):
        self.calls.append((actor, prioritize))

    def poll_all(self, db, actor: str) -> int:
        return 0

    def poll_tracked_actors(self, db, default_actor: str | None = None) -> dict[str, int]:
        return {}


def test_contribution_stats_api(client: TestClient, db_session) -> None:
    db_session.add_all(
        [
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor="~ccleberg",
                repo_name=None,
                resource_id="1",
                external_uid="todo:ticket:1:created",
                occurred_at=datetime(2026, 3, 29, 10, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            ),
            ContributionEvent(
                service="todo",
                event_type="ticket_comment",
                actor="~ccleberg",
                repo_name=None,
                resource_id="1",
                external_uid="todo:ticket:1:comment:2",
                occurred_at=datetime(2026, 3, 30, 11, 0, tzinfo=UTC),
                weight=0.5,
                raw_payload_json=None,
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/contributions/~ccleberg/stats?from=2026-03-28&to=2026-03-30")

    assert response.status_code == 200
    assert response.json()["total_events"] == 2
    assert response.json()["total_score"] == 1.5
    assert response.json()["longest_streak"] == 2
    assert response.json()["current_streak"] == 2
    assert response.json()["is_indexed"] is True
    assert response.json()["indexing_state"] == "indexed"
    assert response.json()["is_recent_window_backfilled"] is False
    assert response.json()["recent_backfill_state"] == "pending"


def test_invalid_date_input_returns_400(client: TestClient) -> None:
    response = client.get("/api/contributions/~ccleberg?from=2026-13-01&to=2026-03-30")

    assert response.status_code == 400
    assert "Invalid date format" in response.json()["detail"]


def test_contribution_routes_use_settings_backed_alias_resolution(settings, db_engine, session_factory) -> None:
    alias_settings = settings.model_copy(update={"actor_aliases_json": {"~ccleberg": ["cmc@example.com"]}})
    app = create_app(alias_settings, engine=db_engine, session_factory=session_factory)
    with session_factory() as session:
        session.add(
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor="~ccleberg",
                repo_name=None,
                resource_id="1",
                external_uid="todo:alias:1",
                occurred_at=datetime(2026, 3, 30, 10, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            )
        )
        session.commit()

    with TestClient(app) as client:
        client.headers.update({"X-API-Key": alias_settings.api_key})
        response = client.get("/api/contributions/cmc@example.com?from=2026-03-30&to=2026-03-30")

    assert response.status_code == 200
    assert response.json()["actor"] == "~ccleberg"


def test_contribution_route_passes_explicit_priority_signal(settings, db_engine, session_factory) -> None:
    poller = RecordingPriorityPoller()
    app = create_app(settings, engine=db_engine, session_factory=session_factory, poller=poller)

    with TestClient(app) as client:
        response = client.get("/api/contributions/~ccleberg?from=2026-03-28&to=2026-03-30&prioritize_self=true")

    assert response.status_code == 200
    assert poller.calls == [("~ccleberg", True)]


def test_contribution_stats_route_keeps_non_prioritized_registration_by_default(settings, db_engine, session_factory) -> None:
    poller = RecordingPriorityPoller()
    app = create_app(settings, engine=db_engine, session_factory=session_factory, poller=poller)

    with TestClient(app) as client:
        response = client.get("/api/contributions/~ccleberg/stats?from=2026-03-28&to=2026-03-30")

    assert response.status_code == 200
    assert poller.calls == [("~ccleberg", False)]
