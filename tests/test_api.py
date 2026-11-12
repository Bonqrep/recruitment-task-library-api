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


def test_chat_page_and_assets_are_served_without_initializing_agent() -> None:
    from app.main import app

    # The autouse fixture changes cwd: assets must resolve relative to app/main.py.
    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "Library AI Assistant" in page.text
        for path, content_type in (
            ("/static/chat.css", "text/css"),
            ("/static/chat.js", "javascript"),
        ):
            asset = client.get(path)
            assert asset.status_code == 200
            assert content_type in asset.headers["content-type"]
        assert "/chat" not in client.get("/openapi.json").json()["paths"]


def test_openapi_documents_books_and_empty_delete_response() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths["/api/v1/books"]) == {"get", "post"}
    assert "/api/v1/books/{serial_number}/borrow" in paths
    assert "/api/v1/books/{serial_number}/return" in paths
    assert (
        "content"
        not in paths["/api/v1/books/{serial_number}"]["delete"]["responses"]["204"]
    )
