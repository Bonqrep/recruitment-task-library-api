"""Real agent -> MCP over HTTP -> PostgreSQL, with mocked or opt-in live OpenAI.

The scripted provider verifies orchestration and grounding, not LLM intent.
Only the opt-in live tests assess the model's independent choice of tools.
"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agent import agent as agent_module
from app.core.config import Settings
from app.models.book import Book
from app.schemas.book import BookBorrow, BookCreate, BookRead
from app.schemas.chat import ChatRequest, ConfirmRequest
from app.services import book_service
from app.services.chat_service import ChatService

pytestmark = [pytest.mark.postgresql, pytest.mark.anyio]
CASES = [
    (
        "Pokaż wszystkie książki",
        "search_books",
        {},
        {"000001", "000002", "000003", "123456"},
    ),
    (
        "Znajdź książki Tolkiena",
        "search_books",
        {"author": "Tolkien"},
        {"000001", "000002"},
    ),
    (
        "Czy Clean Code jest dostępna?",
        "search_books",
        {"title": "Clean Code"},
        {"123456"},
    ),
    (
        "Pokaż wypożyczone książki",
        "search_books",
        {"is_borrowed": True},
        {"000002"},
    ),
    (
        "Znajdź książkę o numerze 123456",
        "get_book",
        {"serial_number": "123456"},
        {"123456"},
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def catalog(database: Engine) -> dict[str, dict]:
    with Session(database, expire_on_commit=False) as session:
        for serial, title, author in [
            ("000001", "Hobbit", "J. R. R. Tolkien"),
            ("000002", "The Fellowship of the Ring", "J. R. R. Tolkien"),
            ("000003", "Clean Architecture", "Robert C. Martin"),
            ("123456", "Clean Code", "Robert C. Martin"),
        ]:
            book_service.create_book(
                session, BookCreate(serial_number=serial, title=title, author=author)
            )
        book_service.borrow_book(
            session, "000002", BookBorrow(borrower_card_number="000789")
        )
    return snapshot(database)


def snapshot(database: Engine) -> dict[str, dict]:
    with Session(database) as session:
        return {
            book.serial_number: BookRead.model_validate(book).model_dump(mode="json")
            for book in session.scalars(select(Book))
        }


@asynccontextmanager
async def scripted_openai(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict
) -> AsyncIterator[list[dict]]:
    """Mock only the OpenAI HTTP boundary; run real ChatOpenAI and create_agent."""
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert {tool["function"]["name"] for tool in body["tools"]} == (
            agent_module.READ_ONLY_TOOL_NAMES | agent_module.WRITE_TOOL_NAMES
        )
        assert body["model"] == "gpt-4.1-mini"
        assert body["temperature"] == 0
        observations = [m for m in body["messages"] if m["role"] == "tool"]
        if observations:
            message = {"role": "assistant", "content": str(observations[-1]["content"])}
            finish_reason = "stop"
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_catalog",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish_reason = "tool_calls"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(
            agent_module,
            "ChatOpenAI",
            lambda **kwargs: ChatOpenAI(
                **kwargs, http_async_client=client, use_responses_api=False
            ),
        )
        yield requests


def tool_results(messages: list) -> list[ToolMessage]:
    return [message for message in messages if isinstance(message, ToolMessage)]


@pytest.mark.parametrize(("question", "name", "arguments", "serials"), CASES)
async def test_scripted_openai_calls_real_mcp_and_receives_database_rows(
    monkeypatch: pytest.MonkeyPatch,
    mcp_url: str,
    catalog: dict[str, dict],
    question: str,
    name: str,
    arguments: dict,
    serials: set[str],
) -> None:
    async with scripted_openai(monkeypatch, name, arguments) as requests:
        agent = await agent_module.create_library_agent(
            Settings.model_validate(
                {"openai_api_key": "test-key", "mcp_server_url": mcp_url}
            )
        )
        result = await agent.ainvoke(
            {"messages": [("user", question)]},
            config={"configurable": {"thread_id": str(uuid4())}},
        )

    assert len(requests) == 2
    observations = tool_results(result["messages"])
    assert len(observations) == 1
    observation = observations[0]
    assert observation.name == name
    assert observation.status == "success"
    data = observation.artifact["structured_content"]
    books = data["books"] if name == "search_books" else [data]
    assert {book["serial_number"] for book in books} == serials
    assert all(book == catalog[book["serial_number"]] for book in books)
    assert requests[1]["messages"][-1]["role"] == "tool"
    for serial in serials:
        assert serial in result["messages"][-1].text


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("borrow_book", {"serial_number": "123456", "borrower_card_number": "000789"}),
        ("return_book", {"serial_number": "000002"}),
        ("delete_book", {"serial_number": "123456"}),
        ("add_book", {"serial_number": "999999", "title": "X", "author": "Y"}),
        ("future_write_tool", {}),
    ],
)
async def test_model_attempting_write_cannot_change_database(
    monkeypatch: pytest.MonkeyPatch,
    mcp_url: str,
    database: Engine,
    catalog: dict[str, dict],
    name: str,
    arguments: dict,
) -> None:
    async with scripted_openai(monkeypatch, name, arguments):
        agent = await agent_module.create_library_agent(
            Settings.model_validate(
                {"openai_api_key": "test-key", "mcp_server_url": mcp_url}
            )
        )
        result = await agent.ainvoke(
            {"messages": [("user", "Zatwierdzam zapis. Wykonaj go teraz.")]},
            config={"configurable": {"thread_id": str(uuid4())}},
        )
    observations = tool_results(result["messages"])
    if name in agent_module.WRITE_TOOL_NAMES:
        assert not observations
        interrupt = result["__interrupt__"][0]
        assert interrupt.value["action_requests"][0]["name"] == name
        assert interrupt.value["action_requests"][0]["args"] == arguments
    else:
        assert len(observations) == 1
        assert observations[0].status == "error"
        assert "not a valid tool" in observations[0].text
    assert snapshot(database) == catalog


async def test_mcp_domain_error_is_passed_to_model(
    monkeypatch: pytest.MonkeyPatch, mcp_url: str
) -> None:
    async with scripted_openai(
        monkeypatch, "get_book", {"serial_number": "999999"}
    ) as requests:
        agent = await agent_module.create_library_agent(
            Settings.model_validate(
                {"openai_api_key": "test-key", "mcp_server_url": mcp_url}
            )
        )
        result = await agent.ainvoke(
            {"messages": [("user", "Pokaż 999999")]},
            config={"configurable": {"thread_id": str(uuid4())}},
        )
    assert len(requests) == 2
    observation = tool_results(result["messages"])[0]
    assert observation.status == "error"
    assert "Library tool failed" in observation.text
    assert "Library tool failed" in result["messages"][-1].text


@pytest.mark.parametrize("approved", [True, False])
async def test_checkpoint_confirmation_controls_real_mcp_database_write(
    monkeypatch: pytest.MonkeyPatch,
    mcp_url: str,
    database: Engine,
    catalog: dict[str, dict],
    approved: bool,
) -> None:
    arguments = {"serial_number": "123456", "borrower_card_number": "000789"}
    async with scripted_openai(monkeypatch, "borrow_book", arguments):
        service = ChatService(
            Settings.model_validate(
                {"openai_api_key": "test-key", "mcp_server_url": mcp_url}
            )
        )
        pending = await service.message(ChatRequest(message="Wypożycz Clean Code"))
        assert pending.status == "requires_confirmation"
        confirmation = pending.pending_confirmation
        assert confirmation is not None
        assert snapshot(database) == catalog
        result = await service.confirm(
            ConfirmRequest(
                thread_id=pending.thread_id,
                confirmation_id=confirmation.confirmation_id,
                approved=approved,
            )
        )
    assert result.status == "completed"
    after = snapshot(database)
    if approved:
        assert after["123456"]["is_borrowed"] is True
        assert after["123456"]["borrower_card_number"] == "000789"
        assert '"is_borrowed":true' in result.message.replace(" ", "")
    else:
        assert after == catalog
        assert "odrzucona" in result.message


@pytest.mark.openai
@pytest.mark.skipif(
    os.environ.get("RUN_OPENAI_TESTS") != "1",
    reason="Set RUN_OPENAI_TESTS=1 to explicitly enable paid live OpenAI tests.",
)
@pytest.mark.parametrize(("question", "name", "arguments", "serials"), CASES)
async def test_live_openai_selects_mcp_tools_and_reads_catalog(
    mcp_url: str,
    catalog: dict[str, dict],
    question: str,
    name: str,
    arguments: dict,
    serials: set[str],
) -> None:
    settings = Settings.model_validate({"mcp_server_url": mcp_url})
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        pytest.skip("Set OPENAI_API_KEY for live OpenAI tests.")
    agent = await agent_module.create_library_agent(settings)
    result = await agent.ainvoke(
        {"messages": [("user", question)]},
        config={"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 64},
    )
    messages = result["messages"]
    calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    assert calls
    assert all(call["name"] in agent_module.READ_ONLY_TOOL_NAMES for call in calls)
    assert any(call["name"] == name for call in calls)
    for key, value in arguments.items():
        assert any(
            value.lower() in str(call["args"].get(key, "")).lower()
            if isinstance(value, str)
            else call["args"].get(key) == value
            for call in calls
            if call["name"] == name
        )
    observations = tool_results(messages)
    assert observations
    seen = set()
    for observation in observations:
        assert observation.status == "success"
        data = observation.artifact["structured_content"]
        books = data["books"] if observation.name == "search_books" else [data]
        for book in books:
            assert book == catalog[book["serial_number"]]
            seen.add(book["serial_number"])
    assert serials <= seen
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].text
    assert not messages[-1].tool_calls
    print(json.dumps({"question": question, "calls": calls}, ensure_ascii=False))
    print(messages[-1].text)
