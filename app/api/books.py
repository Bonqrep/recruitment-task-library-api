from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.book import Book
from app.schemas.book import BookCreate, BookRead
from app.schemas.types import SixDigitIdentifier
from app.services import book_service

router = APIRouter(prefix="/api/v1/books", tags=["books"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post(
    "",
    response_model=BookRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a book",
    responses={409: {"description": "A book with this serial number already exists."}},
)
def create_book(data: BookCreate, session: DatabaseSession) -> Book:
    return book_service.create_book(session, data)


@router.get("", response_model=list[BookRead], summary="List books")
def list_books(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Book]:
    """List books ordered by serial number, using limit and offset pagination."""
    return book_service.list_books(session, limit=limit, offset=offset)


@router.get(
    "/{serial_number}",
    response_model=BookRead,
    summary="Get a book",
    responses={404: {"description": "Book not found."}},
)
def get_book(serial_number: SixDigitIdentifier, session: DatabaseSession) -> Book:
    return book_service.get_book(session, serial_number)


@router.delete(
    "/{serial_number}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    summary="Delete a book",
    responses={
        404: {"description": "Book not found."},
        409: {"description": "Borrowed books cannot be deleted."},
    },
)
def delete_book(
    serial_number: SixDigitIdentifier, session: DatabaseSession
) -> Response:
    """Delete a book only after it has been returned (business rule)."""
    book_service.delete_book(session, serial_number)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
