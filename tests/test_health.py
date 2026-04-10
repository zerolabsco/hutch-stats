from fastapi.testclient import TestClient

from srht_contrib.main import create_app


def test_health_endpoint() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_scheduler_is_disabled_by_default(client: TestClient) -> None:
    assert client.app.state.scheduler is None
