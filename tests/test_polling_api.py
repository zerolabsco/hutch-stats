from datetime import UTC, datetime

from fastapi.testclient import TestClient

from srht_contrib.config import Settings
from srht_contrib.main import create_app
from srht_contrib.models import ContributionEvent
from srht_contrib.services.srht_client import SourceHutClientError


class _Closable:
    def close(self) -> None:
        return None


class InsertingPoller:
    def __init__(self) -> None:
        service = type("Service", (), {"client": _Closable()})()
        self.todo_service = service
        self.git_service = service

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


class FailingPoller:
    def __init__(self) -> None:
        service = type("Service", (), {"client": _Closable()})()
        self.todo_service = service
        self.git_service = service

    def poll_all(self, db, actor: str) -> int:
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
    assert calendar_response.json()["days"] == [{"date": "2026-03-30", "count": 1, "score": 1.0}]


def test_manual_poll_maps_sourcehut_failures_to_502(settings: Settings, db_engine, session_factory) -> None:
    app = create_app(settings, engine=db_engine, session_factory=session_factory, poller=FailingPoller())
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": settings.api_key})
        response = client.post("/api/contributions/poll?actor=~ccleberg")

    assert response.status_code == 502
    assert "SourceHut polling failed" in response.json()["detail"]
