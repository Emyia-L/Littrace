from fastapi.testclient import TestClient

from littrace.api.app import create_app


def test_health_endpoint() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200

    assert response.json() == {
        "status": "ok",
        "service": "littrace-api",
    }