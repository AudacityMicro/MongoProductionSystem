from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_index() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Mongo Production System" in response.text

