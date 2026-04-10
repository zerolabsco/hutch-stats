from fastapi.testclient import TestClient


def test_create_list_get_update_delete_tracked_repositories(client: TestClient) -> None:
    create_response = client.post(
        "/api/repositories",
        json={"actor": "~ccleberg", "repo_name": "Hutch"},
    )

    assert create_response.status_code == 201
    assert create_response.json()["repo_name"] == "~ccleberg/Hutch"

    repository_id = create_response.json()["id"]

    get_response = client.get(f"/api/repositories/{repository_id}")
    assert get_response.status_code == 200
    assert get_response.json()["repo_name"] == "~ccleberg/Hutch"

    update_response = client.patch(
        f"/api/repositories/{repository_id}",
        json={"repo_name": "~ccleberg/cleberg.net"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["repo_name"] == "~ccleberg/cleberg.net"

    list_response = client.get("/api/repositories?actor=~ccleberg")
    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "id": repository_id,
            "service": "git",
            "actor": "~ccleberg",
            "repo_name": "~ccleberg/cleberg.net",
        }
    ]

    delete_response = client.delete(f"/api/repositories/{repository_id}")
    assert delete_response.status_code == 204

    missing_response = client.get(f"/api/repositories/{repository_id}")
    assert missing_response.status_code == 404


def test_repository_validation_and_conflicts(client: TestClient) -> None:
    invalid_response = client.post(
        "/api/repositories",
        json={"actor": "~ccleberg", "repo_name": "   "},
    )
    assert invalid_response.status_code == 422

    first_response = client.post(
        "/api/repositories",
        json={"actor": "~ccleberg", "repo_name": "Hutch"},
    )
    second_response = client.post(
        "/api/repositories",
        json={"actor": "~ccleberg", "repo_name": "~ccleberg/Hutch"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


def test_same_repository_can_be_tracked_by_different_actors(client: TestClient) -> None:
    first_response = client.post(
        "/api/repositories",
        json={"actor": "~ccleberg", "repo_name": "Hutch"},
    )
    second_response = client.post(
        "/api/repositories",
        json={"actor": "~other", "repo_name": "Hutch"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["repo_name"] == "~ccleberg/Hutch"
    assert second_response.json()["repo_name"] == "~other/Hutch"
