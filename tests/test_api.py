from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_returns_swagger_ui() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text
