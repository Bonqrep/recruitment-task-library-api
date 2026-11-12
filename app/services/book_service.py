import logging
from datetime import UTC, datetime

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)
from app.models.book import Book
from app.schemas.book import BookBorrow, BookCreate

logger = logging.getLogger(__name__)


def create_book(session: Session, data: BookCreate) -> Book:
    book = Book(**data.model_dump())
    try:
        with session.begin():
            session.add(book)
    except IntegrityError as exc:
        if (
            isinstance(exc.orig, UniqueViolation)
            and exc.orig.diag.constraint_name == "books_pkey"
        ):
            raise BookAlreadyExistsError(data.serial_number) from exc
        raise
    logger.info("Created book %s", book.serial_number)
    return book


def get_book(session: Session, serial_number: str) -> Book:
    book = session.scalar(select(Book).where(Book.serial_number == serial_number))
    if book is None:
        raise BookNotFoundError(serial_number)
    return book


def list_books(session: Session, limit: int = 100, offset: int = 0) -> list[Book]:
    return list(
        session.scalars(
            select(Book).order_by(Book.serial_number).limit(limit).offset(offset)
        )
    )


def delete_book(session: Session, serial_number: str) -> None:
    with session.begin():
        book = _get_book_for_update(session, serial_number)
        if book.is_borrowed:
            raise BookBorrowedError(serial_number)
        session.delete(book)
    logger.info("Deleted book %s", serial_number)


def borrow_book(session: Session, serial_number: str, data: BookBorrow) -> Book:
    with session.begin():
        book = _get_book_for_update(session, serial_number)
        if book.is_borrowed:
            raise BookAlreadyBorrowedError(serial_number)
        book.is_borrowed = True
        book.borrower_card_number = data.borrower_card_number
        book.borrowed_at = datetime.now(UTC)
    logger.info("Borrowed book %s", serial_number)
    return book


def return_book(session: Session, serial_number: str) -> Book:
    with session.begin():
        book = _get_book_for_update(session, serial_number)
        if not book.is_borrowed:
            raise BookNotBorrowedError(serial_number)
        book.is_borrowed = False
        book.borrower_card_number = None
        book.borrowed_at = None
    logger.info("Returned book %s", serial_number)
    return book


def _get_book_for_update(session: Session, serial_number: str) -> Book:
    """Read current state under a row lock held by the caller's transaction."""
    book = session.scalar(
        select(Book)
        .where(Book.serial_number == serial_number)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if book is None:
        raise BookNotFoundError(serial_number)
    return book
