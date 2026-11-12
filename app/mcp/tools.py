"""Validated MCP adapters; all database operations belong to book_service."""

import json
import logging
from collections.abc import Callable
from functools import partial
from typing import Annotated, Literal

from anyio import to_thread
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ClientCapabilities, ElicitationCapability, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)
from app.db import session as db_session
from app.schemas.book import BookBorrow, BookCreate, BookRead
from app.schemas.types import BookText, SixDigitIdentifier
from app.services import book_service

logger = logging.getLogger(__name__)
DOMAIN_ERRORS = (
    BookAlreadyBorrowedError,
    BookAlreadyExistsError,
    BookBorrowedError,
    BookNotBorrowedError,
    BookNotFoundError,
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


class SearchBooksResult(BaseModel):
    books: list[BookRead]


class DeleteBookResult(BaseModel):
    serial_number: SixDigitIdentifier
    deleted: Literal[True] = True


class WriteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(
        strict=True,
        description="Confirm this exact database change. Select true only to approve.",
    )


def _with_session[T](operation: Callable[[Session], T]) -> T:
    try:
        with db_session.SessionLocal() as session:
            # Serialize ORM results inside operation, before closing the session.
            return operation(session)
    except DOMAIN_ERRORS as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Library MCP database operation failed")
        raise ToolError("Library operation failed. Please try again later.") from exc


async def _run[T](operation: Callable[[Session], T]) -> T:
    # SQLAlchemy uses synchronous I/O; keep it off the MCP event loop.
    return await to_thread.run_sync(partial(_with_session, operation))


async def _confirm(ctx: Context, action: str, arguments: dict[str, str]) -> None:
    if not ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability(form={}))
    ):
        raise ToolError("User confirmation requires MCP form elicitation support.")
    try:
        result = await ctx.elicit(
            message=(
                f"Approve library operation '{action}' with these exact parameters?\n"
                + json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            ),
            schema=WriteConfirmation,
        )
    except Exception as exc:
        logger.exception("MCP user confirmation failed")
        raise ToolError("Could not obtain user confirmation. No change made.") from exc
    if result.action != "accept" or result.data.confirmed is not True:
        raise ToolError("Operation not approved by the user. No change made.")


def register_tools(server: FastMCP) -> None:
    @server.tool(annotations=READ_ONLY)
    async def search_books(
        title: BookText | None = None,
        author: BookText | None = None,
        is_borrowed: StrictBool | None = None,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20,
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
    ) -> SearchBooksResult:
        """Search by case-insensitive title/author fragments and borrowing state."""

        def operation(session: Session) -> SearchBooksResult:
            books = book_service.list_books(
                session,
                title=title,
                author=author,
                is_borrowed=is_borrowed,
                limit=limit,
                offset=offset,
            )
            return SearchBooksResult(books=[BookRead.model_validate(b) for b in books])

        return await _run(operation)

    @server.tool(annotations=READ_ONLY)
    async def get_book(serial_number: SixDigitIdentifier) -> BookRead:
        """Get a book by its six-digit serial number, preserving leading zeroes."""
        return await _run(
            lambda session: BookRead.model_validate(
                book_service.get_book(session, serial_number)
            )
        )

    @server.tool(annotations=WRITE)
    async def add_book(
        serial_number: SixDigitIdentifier,
        title: BookText,
        author: BookText,
        ctx: Context,
    ) -> BookRead:
        """Add a book after the user approves its serial number, title and author."""
        data = BookCreate(serial_number=serial_number, title=title, author=author)
        await _confirm(ctx, "add_book", data.model_dump())
        return await _run(
            lambda session: BookRead.model_validate(
                book_service.create_book(session, data)
            )
        )

    @server.tool(annotations=WRITE)
    async def borrow_book(
        serial_number: SixDigitIdentifier,
        borrower_card_number: SixDigitIdentifier,
        ctx: Context,
    ) -> BookRead:
        """Borrow an available book after user approval of the book and card numbers."""
        data = BookBorrow(borrower_card_number=borrower_card_number)
        await _confirm(
            ctx, "borrow_book", {"serial_number": serial_number, **data.model_dump()}
        )
        return await _run(
            lambda session: BookRead.model_validate(
                book_service.borrow_book(session, serial_number, data)
            )
        )

    @server.tool(annotations=WRITE)
    async def return_book(serial_number: SixDigitIdentifier, ctx: Context) -> BookRead:
        """Return a borrowed book after explicit user approval of its serial number."""
        await _confirm(ctx, "return_book", {"serial_number": serial_number})
        return await _run(
            lambda session: BookRead.model_validate(
                book_service.return_book(session, serial_number)
            )
        )

    @server.tool(annotations=WRITE)
    async def delete_book(
        serial_number: SixDigitIdentifier, ctx: Context
    ) -> DeleteBookResult:
        """Delete an available book after user approval of its serial number."""
        await _confirm(ctx, "delete_book", {"serial_number": serial_number})

        def operation(session: Session) -> DeleteBookResult:
            book_service.delete_book(session, serial_number)
            return DeleteBookResult(serial_number=serial_number)

        return await _run(operation)
