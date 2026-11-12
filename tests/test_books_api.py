from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)
from app.models.book import Book
from app.schemas.book import BookBorrow
from app.services import book_service


@pytest.fixture
def session() -> Mock:
    return Mock(spec=Session)


@pytest.fixture
def client(session: Mock) -> Iterator[TestClient]:
    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        del app.dependency_overrides[get_db]


@pytest.fixture
def book() -> Book:
    return Book(
        serial_number="000123",
        title="Solaris",
        author="Lem",
        is_borrowed=False,
        borrower_card_number=None,
        borrowed_at=None,
    )


def test_borrow_book(
    client: TestClient, session: Mock, book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    book.is_borrowed = True
    book.borrower_card_number = "000456"
    book.borrowed_at = datetime(2026, 11, 12, 12, tzinfo=UTC)
    borrow = Mock(return_value=book)
    monkeypatch.setattr(book_service, "borrow_book", borrow)

    response = client.post(
        "/api/v1/books/000123/borrow", json={"borrower_card_number": "000456"}
    )

    assert response.status_code == 200
    assert response.json()["is_borrowed"] is True
    assert response.json()["borrower_card_number"] == "000456"
    assert response.json()["borrowed_at"] == "2026-11-12T12:00:00Z"
    borrow.assert_called_once_with(
        session, "000123", BookBorrow(borrower_card_number="000456")
    )


def test_return_book_without_body(
    client: TestClient, session: Mock, book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    return_book = Mock(return_value=book)
    monkeypatch.setattr(book_service, "return_book", return_book)

    response = client.post("/api/v1/books/000123/return")

    assert response.status_code == 200
    assert response.json()["is_borrowed"] is False
    assert response.json()["borrower_card_number"] is None
    assert response.json()["borrowed_at"] is None
    return_book.assert_called_once_with(session, "000123")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/12345", None),
        ("get", "?limit=0", None),
        ("post", "", {"serial_number": "000123", "title": "", "author": "Lem"}),
        ("post", "/000123/borrow", {}),
        ("post", "/000123/borrow", {"borrower_card_number": "12345"}),
        ("post", "/000123/borrow", {"borrower_card_number": "12345a"}),
    ],
)
def test_invalid_requests_return_422(
    client: TestClient, session: Mock, method: str, path: str, body: object
) -> None:
    response = client.request(method, f"/api/v1/books{path}", json=body)

    assert response.status_code == 422
    assert session.mock_calls == []


@pytest.mark.parametrize(
    ("path", "service_name", "error", "status_code"),
    [
        ("", "get_book", BookNotFoundError("000123"), 404),
        ("/borrow", "borrow_book", BookAlreadyBorrowedError("000123"), 409),
        ("/return", "return_book", BookNotBorrowedError("000123"), 409),
    ],
)
def test_domain_errors_use_central_handlers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    service_name: str,
    error: Exception,
    status_code: int,
) -> None:
    monkeypatch.setattr(book_service, service_name, Mock(side_effect=error))

    response = client.request(
        "post" if path else "get",
        f"/api/v1/books/000123{path}",
        json={"borrower_card_number": "000456"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}
