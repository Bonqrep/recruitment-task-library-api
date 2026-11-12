import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
)
from app.models.book import Book
from app.schemas.book import BookBorrow, BookCreate
from app.services import book_service

pytestmark = pytest.mark.postgresql


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Migrate a separate schema per test; never modify existing application data."""
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests.")

    from app.db import session as session_module

    schema = f"test_books_{uuid4().hex}"
    admin = create_engine(database_url)
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        monkeypatch.setattr(session_module, "engine", engine)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def client(database: Engine) -> Iterator[TestClient]:
    from app.db.session import get_db
    from app.main import app

    def get_test_db() -> Iterator[Session]:
        with Session(database, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        del app.dependency_overrides[get_db]


def book_data(serial_number: str = "000123") -> BookCreate:
    return BookCreate(serial_number=serial_number, title="Solaris", author="Lem")


def test_http_crud_persists_data(client: TestClient, database: Engine) -> None:
    data = book_data().model_dump()
    created = client.post("/api/v1/books", json=data)
    assert created.status_code == 201
    expected = {
        **data,
        "is_borrowed": False,
        "borrower_card_number": None,
        "borrowed_at": None,
    }
    assert created.json() == expected
    with Session(database) as session:
        assert session.scalar(select(Book).where(Book.serial_number == "000123"))
    assert client.get("/api/v1/books/000123").json() == expected
    assert client.get("/api/v1/books").json() == [expected]

    deleted = client.delete("/api/v1/books/000123")
    assert deleted.status_code == 204
    assert deleted.content == b""
    with Session(database) as session:
        assert session.scalar(select(Book)) is None
    assert client.get("/api/v1/books").json() == []
    assert client.get("/api/v1/books/000123").status_code == 404
    assert client.delete("/api/v1/books/000123").status_code == 404


def test_duplicate_rolls_back_and_session_can_be_reused(database: Engine) -> None:
    with Session(database, expire_on_commit=False) as session:
        book_service.create_book(session, book_data())
        with pytest.raises(BookAlreadyExistsError) as error:
            book_service.create_book(session, book_data())

        assert isinstance(error.value.__cause__, IntegrityError)
        assert isinstance(error.value.__cause__.orig, UniqueViolation)
        assert not session.in_transaction()
        book_service.create_book(session, book_data("000124"))
        assert len(book_service.list_books(session)) == 2


def test_delete_locks_row_and_rechecks_committed_borrowing_state(
    database: Engine,
) -> None:
    with Session(database, expire_on_commit=False) as session:
        book_service.create_book(session, book_data())

    deleting_pid: Queue[int] = Queue()

    def delete() -> None:
        with database.connect() as connection:
            deleting_pid.put(connection.connection.driver_connection.info.backend_pid)
            with Session(connection) as session:
                book_service.delete_book(session, "000123")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(database) as borrower, borrower.begin():
            book = borrower.scalar(select(Book).with_for_update())
            assert book is not None
            book.is_borrowed = True
            book.borrower_card_number = "000456"
            book.borrowed_at = datetime.now(UTC)
            borrower.flush()
            pending_delete = executor.submit(delete)
            pid = deleting_pid.get(timeout=5)
            deadline = monotonic() + 5
            with database.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                while monotonic() < deadline:
                    blocked = observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                        {"pid": pid},
                    )
                    if blocked:
                        break
                    sleep(0.01)
                else:
                    pytest.fail("Delete did not wait for the borrowing transaction.")

        with pytest.raises(BookBorrowedError):
            pending_delete.result(timeout=10)

    with Session(database) as session:
        assert book_service.get_book(session, "000123").is_borrowed is True


def test_pagination_orders_and_limits_books(
    client: TestClient, database: Engine
) -> None:
    with Session(database) as session, session.begin():
        session.add_all(
            Book(**book_data(serial).model_dump())
            for serial in ("000003", "000001", "000002")
        )

    response = client.get("/api/v1/books?limit=1&offset=1")

    assert response.status_code == 200
    assert [book["serial_number"] for book in response.json()] == ["000002"]


def test_http_duplicate_returns_conflict(client: TestClient) -> None:
    data = book_data().model_dump()
    assert client.post("/api/v1/books", json=data).status_code == 201

    response = client.post("/api/v1/books", json={**data, "title": "Changed"})

    assert response.status_code == 409
    assert client.get("/api/v1/books/000123").json()["title"] == "Solaris"


def test_http_borrow_and_return_persist_state(
    client: TestClient, database: Engine
) -> None:
    assert (
        client.post("/api/v1/books", json=book_data().model_dump()).status_code == 201
    )
    before = datetime.now(UTC)
    body = {"borrower_card_number": "000456"}

    borrowed = client.post("/api/v1/books/000123/borrow", json=body)

    assert borrowed.status_code == 200
    timestamp = datetime.fromisoformat(borrowed.json()["borrowed_at"])
    assert timestamp.utcoffset().total_seconds() == 0
    assert before <= timestamp <= datetime.now(UTC)
    with Session(database) as session:
        book = book_service.get_book(session, "000123")
        assert book.is_borrowed is True
        assert book.borrower_card_number == "000456"
        assert book.borrowed_at == timestamp
    assert client.post("/api/v1/books/000123/borrow", json=body).status_code == 409
    assert client.get("/api/v1/books/000123").json() == borrowed.json()
    assert client.delete("/api/v1/books/000123").status_code == 409

    returned = client.post("/api/v1/books/000123/return")

    assert returned.status_code == 200
    assert returned.json()["is_borrowed"] is False
    assert returned.json()["borrower_card_number"] is None
    assert returned.json()["borrowed_at"] is None
    assert client.get("/api/v1/books/000123").json() == returned.json()
    assert client.post("/api/v1/books/000123/return").status_code == 409
    assert client.delete("/api/v1/books/000123").status_code == 204


@pytest.mark.parametrize("action", ["borrow", "return"])
def test_http_borrowing_missing_book(client: TestClient, action: str) -> None:
    response = client.post(
        f"/api/v1/books/000123/{action}", json={"borrower_card_number": "000456"}
    )

    assert response.status_code == 404


def test_concurrent_borrows_have_one_winner(database: Engine) -> None:
    with Session(database, expire_on_commit=False) as session:
        book_service.create_book(session, book_data())
    start = Barrier(2)

    def borrow(card: str) -> Book | BookAlreadyBorrowedError:
        with Session(database, expire_on_commit=False) as session:
            start.wait(timeout=5)
            try:
                return book_service.borrow_book(
                    session, "000123", BookBorrow(borrower_card_number=card)
                )
            except BookAlreadyBorrowedError as exc:
                assert not session.in_transaction()
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(borrow, card) for card in ("000456", "000789")]
        results = [future.result(timeout=10) for future in futures]

    winners = [result for result in results if isinstance(result, Book)]
    assert len(winners) == 1
    assert sum(isinstance(result, BookAlreadyBorrowedError) for result in results) == 1
    with Session(database) as session:
        book = book_service.get_book(session, "000123")
        assert book.is_borrowed is True
        assert book.borrower_card_number == winners[0].borrower_card_number
