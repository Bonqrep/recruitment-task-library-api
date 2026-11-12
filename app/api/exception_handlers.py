from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)


async def book_not_found_handler(
    request: Request, exc: BookNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)}
    )


async def book_conflict_handler(
    request: Request,
    exc: (
        BookAlreadyExistsError
        | BookBorrowedError
        | BookAlreadyBorrowedError
        | BookNotBorrowedError
    ),
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BookNotFoundError, book_not_found_handler)
    app.add_exception_handler(BookAlreadyExistsError, book_conflict_handler)
    app.add_exception_handler(BookBorrowedError, book_conflict_handler)
    app.add_exception_handler(BookAlreadyBorrowedError, book_conflict_handler)
    app.add_exception_handler(BookNotBorrowedError, book_conflict_handler)
