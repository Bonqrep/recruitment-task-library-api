"""Real MCP client -> Streamable HTTP -> separate process -> PostgreSQL."""

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.context import RequestContext
from mcp.types import (
    CallToolResult,
    ElicitRequestFormParams,
    ElicitRequestParams,
    ElicitResult,
    TextContent,
)
from pydantic import TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models.book import Book
from app.schemas.book import BookBorrow, BookCreate, BookRead
from app.services import book_service

pytestmark = [pytest.mark.postgresql, pytest.mark.anyio]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def data(result: CallToolResult) -> dict[str, object]:
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def assert_error(result: CallToolResult, message: str) -> None:
    assert result.isError
    assert result.content
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert message in content.text
    assert "Traceback" not in content.text


async def test_http_mcp_crud_and_conflicts(mcp_url: str, database: Engine) -> None:
    approvals = []

    async def approve(
        context: RequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        # Simulate explicit user approval in this test only.
        assert isinstance(params, ElicitRequestFormParams)
        assert "000123" in params.message
        assert params.requestedSchema["properties"]["confirmed"]["type"] == "boolean"
        approvals.append(params.message)
        return ElicitResult(action="accept", content={"confirmed": True})

    async with (
        streamable_http_client(mcp_url) as (read, write, _),
        ClientSession(read, write, elicitation_callback=approve) as client,
    ):
        await client.initialize()
        tools = await client.list_tools()
        assert len(tools.tools) == 6
        book = {"serial_number": "000123", "title": "Clean Code", "author": "Martin"}
        created = data(await client.call_tool("add_book", book))
        assert created == {
            **book,
            "is_borrowed": False,
            "borrower_card_number": None,
            "borrowed_at": None,
        }
        with Session(database) as session:
            assert book_service.get_book(session, "000123").title == "Clean Code"

        assert data(await client.call_tool("search_books", {}))["books"] == [created]
        assert (
            data(await client.call_tool("get_book", {"serial_number": "000123"}))
            == created
        )
        assert_error(await client.call_tool("add_book", book), "already exists")

        borrow_args = {"serial_number": "000123", "borrower_card_number": "000456"}
        borrowed = data(await client.call_tool("borrow_book", borrow_args))
        assert borrowed["is_borrowed"] is True
        assert borrowed["borrower_card_number"] == "000456"
        borrowed_at = borrowed["borrowed_at"]
        assert isinstance(borrowed_at, str)
        assert borrowed_at.endswith("Z")
        assert "000456" in approvals[-1]
        with Session(database) as session:
            assert (
                book_service.get_book(session, "000123").borrower_card_number
                == "000456"
            )

        assert_error(
            await client.call_tool("borrow_book", borrow_args), "already borrowed"
        )
        serial = {"serial_number": "000123"}
        assert_error(await client.call_tool("delete_book", serial), "cannot be deleted")
        assert data(await client.call_tool("get_book", serial)) == borrowed
        returned = data(await client.call_tool("return_book", serial))
        assert returned == created
        assert_error(await client.call_tool("return_book", serial), "not borrowed")
        assert data(await client.call_tool("delete_book", serial)) == {
            "serial_number": "000123",
            "deleted": True,
        }
        with Session(database) as session:
            assert session.get(Book, "000123") is None
        for name in ("get_book", "delete_book", "return_book"):
            assert_error(await client.call_tool(name, serial), "not found")
        assert_error(await client.call_tool("borrow_book", borrow_args), "not found")
        before = len(approvals)
        assert_error(
            await client.call_tool("get_book", {"serial_number": "12345"}), "validation"
        )
        assert len(approvals) == before
        assert data(await client.call_tool("search_books", {})) == {"books": []}


async def test_http_mcp_search_filters_and_pagination(
    mcp_url: str, database: Engine
) -> None:
    titles = [
        ("Clean Code", "Robert Martin"),
        ("clean Architecture", "Robert MARTIN"),
        ("100% Correct", "A_Percent"),
        ("under_score", "Somebody"),
        ("back\\slash", "Somebody"),
        ("Hobbit", "Tolkien"),
        *((f"Volume {i}", "Anon") for i in range(20)),
    ]
    with Session(database, expire_on_commit=False) as session:
        for i in reversed(range(len(titles))):
            title, author = titles[i]
            book_service.create_book(
                session, BookCreate(serial_number=f"{i:06}", title=title, author=author)
            )
        book_service.borrow_book(
            session, "000001", BookBorrow(borrower_card_number="000456")
        )

    async with (
        streamable_http_client(mcp_url) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        books_adapter = TypeAdapter(list[BookRead])

        async def search(**arguments: object) -> list[BookRead]:
            result = data(await client.call_tool("search_books", arguments))
            return books_adapter.validate_python(result["books"])

        first_page = await search()
        assert len(first_page) == 20
        assert [b.serial_number for b in first_page] == [f"{i:06}" for i in range(20)]
        assert len(await search(limit=100)) == 26
        assert len(await search(offset=20)) == 6
        assert await search(offset=26) == []
        assert [b.serial_number for b in await search(title="CLEAN")] == [
            "000000",
            "000001",
        ]
        assert len(await search(author="mArTiN")) == 2
        assert [
            b.serial_number
            for b in await search(title="clean", author="ROBERT", is_borrowed=False)
        ] == ["000000"]
        assert [b.serial_number for b in await search(is_borrowed=True)] == ["000001"]
        assert await search(title="Clean", author="Tolkien") == []
        assert [b.title for b in await search(title="%")] == ["100% Correct"]
        assert [b.title for b in await search(title="_")] == ["under_score"]
        assert [b.title for b in await search(title="\\")] == ["back\\slash"]
        assert [b.title for b in await search(author="_")] == ["100% Correct"]
