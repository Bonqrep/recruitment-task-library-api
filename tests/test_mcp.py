from unittest.mock import Mock

import httpx
import pytest
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitRequestParams, ElicitResult, TextContent
from sqlalchemy.orm import Session

from app.core.exceptions import BookNotFoundError
from app.services import book_service

pytestmark = pytest.mark.anyio
WRITE_CALLS = [
    ("add_book", {"serial_number": "000123", "title": "Solaris", "author": "Lem"}),
    ("borrow_book", {"serial_number": "000123", "borrower_card_number": "000456"}),
    ("return_book", {"serial_number": "000123"}),
    ("delete_book", {"serial_number": "000123"}),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_tool_discovery_and_schemas() -> None:
    from app.mcp.server import create_server

    async with create_connected_server_and_client_session(create_server()) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {
        "search_books",
        "get_book",
        *(name for name, _ in WRITE_CALLS),
    }
    for tool in tools.values():
        assert tool.description
        assert tool.outputSchema is not None
        assert tool.outputSchema["type"] == "object"
        assert "ctx" not in tool.inputSchema["properties"]
        assert "confirmed" not in tool.inputSchema["properties"]

    search = tools["search_books"].inputSchema
    assert search["properties"]["limit"]["default"] == 20
    assert search["properties"]["limit"]["maximum"] == 100
    assert search["properties"]["limit"]["minimum"] == 1
    assert not search.get("required")
    serial = tools["get_book"].inputSchema["properties"]["serial_number"]
    assert serial["type"] == "string"
    assert serial["pattern"] == "^[0-9]{6}$"
    assert tools["borrow_book"].inputSchema["required"] == [
        "serial_number",
        "borrower_card_number",
    ]
    read_annotations = tools["get_book"].annotations
    write_annotations = tools["delete_book"].annotations
    assert read_annotations is not None
    assert write_annotations is not None
    assert read_annotations.readOnlyHint is True
    assert write_annotations.destructiveHint is True
    expected = {
        "search_books": ({"title", "author", "is_borrowed", "limit", "offset"}, set()),
        "get_book": ({"serial_number"}, {"serial_number"}),
        "add_book": (
            {"serial_number", "title", "author"},
            {"serial_number", "title", "author"},
        ),
        "borrow_book": (
            {"serial_number", "borrower_card_number"},
            {"serial_number", "borrower_card_number"},
        ),
        "return_book": ({"serial_number"}, {"serial_number"}),
        "delete_book": ({"serial_number"}, {"serial_number"}),
    }
    for name, (properties, required) in expected.items():
        schema = tools[name].inputSchema
        assert set(schema["properties"]) == properties
        assert set(schema.get("required", [])) == required


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_book", {"serial_number": "12345"}),
        ("get_book", {"serial_number": 123456}),
        ("delete_book", {"serial_number": "１２３４５６"}),
        ("borrow_book", {"serial_number": "000123", "borrower_card_number": "abc"}),
        ("add_book", {"serial_number": "000123", "title": " ", "author": "Lem"}),
        ("search_books", {"limit": 101}),
        ("search_books", {"limit": 0}),
        ("search_books", {"limit": True}),
        ("search_books", {"offset": -1}),
        ("search_books", {"title": "\x00"}),
        ("search_books", {"is_borrowed": 1}),
    ],
)
async def test_invalid_parameters_do_not_open_a_session(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, object]
) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    factory = Mock()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    async with create_connected_server_and_client_session(create_server()) as client:
        result = await client.call_tool(name, arguments)

    assert result.isError
    factory.assert_not_called()


@pytest.mark.parametrize(("name", "arguments"), WRITE_CALLS)
async def test_writes_without_elicitation_support_are_blocked(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, str]
) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    factory = Mock()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    async with create_connected_server_and_client_session(create_server()) as client:
        # A model-provided confirmation parameter cannot authorize a write.
        result = await client.call_tool(name, {**arguments, "confirmed": True})

    assert result.isError
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert "elicitation" in content.text
    factory.assert_not_called()


@pytest.mark.parametrize(("name", "arguments"), WRITE_CALLS)
@pytest.mark.parametrize(
    ("action", "content"),
    [
        ("decline", None),
        ("cancel", None),
        ("accept", {"confirmed": False}),
        ("accept", {"confirmed": "true"}),
        ("accept", {}),
    ],
)
async def test_writes_require_explicit_positive_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, str],
    action: str,
    content: dict[str, object] | None,
) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    async def respond(
        context: RequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        assert name in params.message
        assert arguments["serial_number"] in params.message
        return ElicitResult.model_validate({"action": action, "content": content})

    factory = Mock()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    async with create_connected_server_and_client_session(
        create_server(), elicitation_callback=respond
    ) as client:
        result = await client.call_tool(name, arguments)

    assert result.isError
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BookNotFoundError("000123"), "Book '000123' not found."),
        (
            RuntimeError("secret SQL SELECT password FROM users"),
            "Library operation failed",
        ),
    ],
)
async def test_error_mapping_closes_session_and_hides_internal_details(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(spec=Session))
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(book_service, "get_book", Mock(side_effect=error))

    async with create_connected_server_and_client_session(create_server()) as client:
        result = await client.call_tool("get_book", {"serial_number": "000123"})

    assert result.isError
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert expected in content.text
    assert "SELECT" not in content.text
    assert "Traceback" not in content.text
    factory.return_value.__exit__.assert_called_once()


async def test_reads_use_distinct_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    contexts = []

    def new_session() -> Mock:
        context = Mock()
        context.__enter__ = Mock(return_value=Mock(spec=Session))
        context.__exit__ = Mock(return_value=False)
        contexts.append(context)
        return context

    monkeypatch.setattr(db_session, "SessionLocal", new_session)
    listing = Mock(return_value=[])
    monkeypatch.setattr(book_service, "list_books", listing)
    async with create_connected_server_and_client_session(create_server()) as client:
        for _ in range(2):
            result = await client.call_tool("search_books", {})
            assert not result.isError
            assert result.structuredContent == {"books": []}

    assert listing.call_args_list[0].args[0] is not listing.call_args_list[1].args[0]
    assert len(contexts) == 2
    for context in contexts:
        context.__exit__.assert_called_once()


@pytest.mark.parametrize("available", [True, False])
async def test_mcp_health_checks_database_without_exposing_errors(
    monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    from app.db import session as db_session
    from app.mcp.server import create_server

    engine = Mock()
    engine.connect.return_value.__enter__ = Mock(return_value=Mock())
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    if not available:
        engine.connect.side_effect = RuntimeError("private connection credentials")
    monkeypatch.setattr(db_session, "engine", engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_server().streamable_http_app()),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/health")
    assert response.status_code == (200 if available else 503)
    assert response.json() == {"status": "ok" if available else "unavailable"}
    assert "private" not in response.text
