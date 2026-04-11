from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from srht_contrib.config import Settings
from srht_contrib.main import create_app
from srht_contrib.models import ContributionEvent, TrackedActor
from srht_contrib.services.srht_client import SourceHutClientError


class _Closable:
    def close(self) -> None:
        return None


class InsertingPoller:
    def __init__(self) -> None:
        service = type("Service", (), {"client": _Closable()})()
        self.todo_service = service
        self.git_service = service
        self.tracked_poll_calls: list[str] = []

    def track_actor_request(self, db, actor: str, *, update_last_requested: bool = True):
        tracked_actor = db.scalar(select(TrackedActor).where(TrackedActor.actor == actor))
        if tracked_actor is None:
            tracked_actor = TrackedActor(actor=actor, is_active=True)
            db.add(tracked_actor)
        if update_last_requested:
            tracked_actor.last_requested_at = datetime(2026, 3, 30, 9, 0, tzinfo=UTC)
        db.flush()
        return tracked_actor

    def poll_all(self, db, actor: str) -> int:
        db.add(
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor=actor,
                repo_name="todo",
                resource_id="1",
                external_uid="todo:manual:1",
                occurred_at=datetime(2026, 3, 30, 10, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            )
        )
        db.commit()
        return 1

    def poll_tracked_actors(self, db, default_actor: str | None = None) -> dict[str, int]:
        if default_actor is not None:
            self.tracked_poll_calls.append(default_actor)
            return {default_actor: self.poll_all(db, default_actor)}
        return {}


class FailingPoller:
    def __init__(self) -> None:
        service = type("Service", (), {"client": _Closable()})()
        self.todo_service = service
        self.git_service = service

    def track_actor_request(self, db, actor: str, *, update_last_requested: bool = True):
        tracked_actor = db.scalar(select(TrackedActor).where(TrackedActor.actor == actor))
        if tracked_actor is None:
            tracked_actor = TrackedActor(actor=actor, is_active=True)
            db.add(tracked_actor)
        db.flush()
        return tracked_actor

    def poll_all(self, db, actor: str) -> int:
        raise SourceHutClientError("boom")

    def poll_tracked_actors(self, db, default_actor: str | None = None) -> dict[str, int]:
        raise SourceHutClientError("boom")


def test_manual_poll_uses_same_database_session(settings: Settings, db_engine, session_factory) -> None:
    app = create_app(settings, engine=db_engine, session_factory=session_factory, poller=InsertingPoller())
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": settings.api_key})

        poll_response = client.post("/api/contributions/poll?actor=~ccleberg")
        calendar_response = client.get("/api/contributions/~ccleberg?from=2026-03-30&to=2026-03-30")

    assert poll_response.status_code == 200
    assert poll_response.json()["inserted_events"] == 1
    assert calendar_response.status_code == 200
    assert calendar_response.json()["is_indexed"] is True
    assert calendar_response.json()["days"] == [{"date": "2026-03-30", "count": 1, "score": 1.0}]


def test_manual_poll_maps_sourcehut_failures_to_502(settings: Settings, db_engine, session_factory) -> None:
    app = create_app(settings, engine=db_engine, session_factory=session_factory, poller=FailingPoller())
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": settings.api_key})
        response = client.post("/api/contributions/poll?actor=~ccleberg")

    assert response.status_code == 502
    assert "SourceHut polling failed" in response.json()["detail"]


def test_scheduler_runs_initial_poll_on_startup(settings: Settings, db_engine, session_factory) -> None:
    scheduler_settings = settings.model_copy(update={"enable_scheduler": True})
    poller = InsertingPoller()
    app = create_app(scheduler_settings, engine=db_engine, session_factory=session_factory, poller=poller)

    with TestClient(app):
        pass

    assert poller.tracked_poll_calls == ["~ccleberg"]


def test_startup_poll_failure_does_not_block_app_start(settings: Settings, db_engine, session_factory) -> None:
    scheduler_settings = settings.model_copy(update={"enable_scheduler": True})
    app = create_app(scheduler_settings, engine=db_engine, session_factory=session_factory, poller=FailingPoller())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
