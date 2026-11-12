import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)

logger = logging.getLogger(__name__)


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled application error", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
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
    app.add_exception_handler(Exception, internal_error_handler)
    app.add_exception_handler(BookNotFoundError, book_not_found_handler)
    app.add_exception_handler(BookAlreadyExistsError, book_conflict_handler)
    app.add_exception_handler(BookBorrowedError, book_conflict_handler)
    app.add_exception_handler(BookAlreadyBorrowedError, book_conflict_handler)
    app.add_exception_handler(BookNotBorrowedError, book_conflict_handler)
