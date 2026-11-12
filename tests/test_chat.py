"""Real agent/checkpointer/HITL/MCP protocol, scripted LLM and mocked database I/O."""

import asyncio
import inspect
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters import tools as mcp_adapter
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import Field

from app.agent import agent as agent_module
from app.core.config import Settings
from app.core.exceptions import BookAlreadyBorrowedError
from app.services.chat_service import ChatService

pytestmark = pytest.mark.anyio
SERIAL = "000123"
CARD = "000456"
BORROW = {"serial_number": SERIAL, "borrower_card_number": CARD}


class ScriptedModel(BaseChatModel):
    responder: Callable
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError("Only the async LLM API is allowed")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(messages)
        response = self.responder(messages)
        if inspect.isawaitable(response):
            response = await response
        return ChatResult(generations=[ChatGeneration(message=response)])


def call(name="borrow_book", args=None):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or BORROW, "id": uuid4().hex}],
    )


def propose_borrow(messages):
    if isinstance(messages[-1], ToolMessage):
        return AIMessage(content="Wypożyczono książkę.")
    return call()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def backend(monkeypatch):
    from app.db import session as db_session
    from app.mcp.server import create_server
    from app.services import book_service

    catalog = {
        SERIAL: {
            "serial_number": SERIAL,
            "title": "Clean Code",
            "author": "Robert Martin",
            "is_borrowed": False,
            "borrower_card_number": None,
            "borrowed_at": None,
        }
    }

    def borrow(session, serial, data):
        book = catalog[serial]
        if book["is_borrowed"]:
            raise BookAlreadyBorrowedError(serial)
        book.update(
            is_borrowed=True,
            borrower_card_number=data.borrower_card_number,
            borrowed_at=datetime.now(UTC),
        )
        return book.copy()

    def add(session, data):
        catalog[data.serial_number] = {
            **data.model_dump(),
            "is_borrowed": False,
            "borrower_card_number": None,
            "borrowed_at": None,
        }
        return catalog[data.serial_number].copy()

    def return_book(session, serial):
        catalog[serial].update(
            is_borrowed=False, borrower_card_number=None, borrowed_at=None
        )
        return catalog[serial].copy()

    mutations = {
        "borrow_book": Mock(side_effect=borrow),
        "create_book": Mock(side_effect=add),
        "return_book": Mock(side_effect=return_book),
        "delete_book": Mock(side_effect=lambda session, serial: catalog.pop(serial)),
    }
    for name, mock in mutations.items():
        monkeypatch.setattr(book_service, name, mock)
    monkeypatch.setattr(
        book_service,
        "list_books",
        Mock(side_effect=lambda *a, **kw: list(catalog.values())),
    )
    monkeypatch.setattr(
        book_service,
        "get_book",
        Mock(side_effect=lambda session, serial: catalog[serial].copy()),
    )
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock())
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(db_session, "SessionLocal", factory)

    @asynccontextmanager
    async def memory_session(connection, *, mcp_callbacks):
        # Each call opens a session inside its own approval scope, as in HTTP.
        async with create_connected_server_and_client_session(
            create_server(), elicitation_callback=mcp_callbacks.elicitation_callback
        ) as session:
            yield session

    monkeypatch.setattr(mcp_adapter, "create_session", memory_session)
    model = ScriptedModel(responder=propose_borrow)
    build_model = Mock(return_value=model)
    monkeypatch.setattr(agent_module, "_create_model", build_model)
    service = ChatService(
        Settings.model_validate({"openai_api_key": "test-secret-key"})
    )
    return service, model, catalog, mutations, build_model


@pytest.fixture
async def client(backend):
    from app.api.chat import get_chat_service
    from app.main import app

    app.dependency_overrides[get_chat_service] = lambda: backend[0]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://localhost",
        ) as client:
            yield client
    finally:
        del app.dependency_overrides[get_chat_service]


def confirmation(response, approved=True):
    body = (
        response.json()
        if isinstance(response, httpx.Response)
        else response.model_dump(mode="json")
    )
    return {
        "thread_id": body["thread_id"],
        "confirmation_id": body["pending_confirmation"]["confirmation_id"],
        "approved": approved,
    }


async def test_new_thread_and_memory_resolve_book_and_ask_for_card(client, backend):
    service, model, catalog, mutations, build_model = backend

    def conversation(messages):
        last = messages[-1]
        if isinstance(last, ToolMessage):
            if last.name == "search_books":
                return AIMessage(content="Tak, Clean Code jest dostępna.")
            return AIMessage(content="Wypożyczono Clean Code.")
        if "Clean Code" in last.content:
            return call("search_books", {"title": "Clean Code"})
        if "Chcę ją" in last.content:
            assert any("Clean Code" in m.text for m in messages[:-1])
            return AIMessage(content="Podaj sześciocyfrowy numer karty czytelnika.")
        # Extract the selected book from the actual prior MCP observation.
        observed = next(m for m in messages if isinstance(m, ToolMessage))
        book = observed.artifact["structured_content"]["books"][0]
        return call(
            args={
                "serial_number": book["serial_number"],
                "borrower_card_number": last.content,
            }
        )

    model.responder = conversation
    first = await client.post("/api/v1/chat", json={"message": "Czy macie Clean Code?"})
    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    thread = first.json()["thread_id"]
    assert UUID(thread).version == 4
    second = await client.post(
        "/api/v1/chat", json={"thread_id": thread, "message": "Chcę ją wypożyczyć"}
    )
    assert second.json()["thread_id"] == thread
    assert "numer karty" in second.json()["message"]
    third = await client.post(
        "/api/v1/chat", json={"thread_id": thread, "message": CARD}
    )
    assert third.json()["status"] == "requires_confirmation"
    assert third.json()["pending_confirmation"]["operations"] == [
        {"name": "borrow_book", "args": BORROW}
    ]
    assert catalog[SERIAL]["is_borrowed"] is False
    mutations["borrow_book"].assert_not_called()
    build_model.assert_called_once()
    checkpoint = service._agent.checkpointer
    approved = await client.post("/api/v1/chat/confirm", json=confirmation(third))
    assert approved.json()["status"] == "completed"
    assert "Wypożyczono" in approved.json()["message"]
    assert catalog[SERIAL]["is_borrowed"] is True
    mutations["borrow_book"].assert_called_once()
    assert service._agent.checkpointer is checkpoint


@pytest.mark.parametrize("matches", [0, 2])
async def test_catalog_observations_control_empty_and_ambiguous_search(
    client, backend, monkeypatch, matches
):
    from app.services import book_service

    _, model, catalog, mutations, _ = backend
    if matches == 0:
        catalog.clear()
    else:
        catalog["000124"] = {
            **catalog[SERIAL],
            "serial_number": "000124",
            "title": "Clean Architecture",
        }

    def respond(messages):
        if not isinstance(messages[-1], ToolMessage):
            return call("search_books", {"title": "Clean"})
        observation = messages[-1]
        assert observation.status == "success"
        books = observation.artifact["structured_content"]["books"]
        assert len(books) == matches
        assert "Nie wymyślaj książek" in messages[0].text
        if not books:
            return AIMessage(content="Nie znaleziono książek pasujących do zapytania.")
        options = "; ".join(f"{b['title']} ({b['serial_number']})" for b in books)
        return AIMessage(content=f"{options}. Którą książkę wybierasz?")

    model.responder = respond
    listing = Mock(wraps=book_service.list_books)
    monkeypatch.setattr(book_service, "list_books", listing)
    response = await client.post(
        "/api/v1/chat", json={"message": "Wypożycz książkę Clean"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    listing.assert_called_once()
    assert listing.call_args.kwargs == {
        "title": "Clean",
        "author": None,
        "is_borrowed": None,
        "limit": 20,
        "offset": 0,
    }
    if matches:
        for book in catalog.values():
            assert book["title"] in response.json()["message"]
            assert book["serial_number"] in response.json()["message"]
        assert "Którą" in response.json()["message"]
    else:
        assert "Nie znaleziono" in response.json()["message"]
        assert "Clean Code" not in response.json()["message"]
    for mutation in mutations.values():
        mutation.assert_not_called()


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("path", ["", "/confirm"])
async def test_docker_peer_requires_explicit_network_and_local_host_origin(
    backend, monkeypatch, allowed, path
):
    from app.api.chat import get_chat_service
    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setenv(
        "CHAT_ALLOWED_NETWORKS", '["172.20.0.0/16"]' if allowed else "[]"
    )
    get_settings.cache_clear()
    app.dependency_overrides[get_chat_service] = lambda: backend[0]
    body = (
        {"message": "Hej"}
        if not path
        else {"thread_id": str(uuid4()), "confirmation_id": "missing", "approved": True}
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("172.20.0.1", 1234)),
            base_url="http://localhost:8000",
        ) as docker_client:
            response = await docker_client.post("/api/v1/chat" + path, json=body)
            assert response.status_code == ((404 if path else 200) if allowed else 403)
            for headers in (
                {"Origin": "http://foreign.example"},
                {"Host": "public.example"},
            ):
                response = await docker_client.post(
                    "/api/v1/chat" + path, json=body, headers=headers
                )
                assert response.status_code == 403
    finally:
        del app.dependency_overrides[get_chat_service]


@pytest.mark.parametrize(
    "name,args,mutation",
    [
        ("borrow_book", BORROW, "borrow_book"),
        (
            "add_book",
            {"serial_number": "000789", "title": "Solaris", "author": "Lem"},
            "create_book",
        ),
        ("return_book", {"serial_number": SERIAL}, "return_book"),
        ("delete_book", {"serial_number": SERIAL}, "delete_book"),
    ],
)
@pytest.mark.parametrize("approved", [True, False])
async def test_every_write_really_interrupts_and_requires_exact_approval(
    client, backend, name, args, mutation, approved
):
    _, model, catalog, mutations, _ = backend
    if name == "return_book":
        catalog[SERIAL].update(
            is_borrowed=True, borrower_card_number=CARD, borrowed_at=datetime.now(UTC)
        )
    before = {k: v.copy() for k, v in catalog.items()}
    model.responder = lambda messages: (
        AIMessage(content="Gotowe.")
        if isinstance(messages[-1], ToolMessage)
        else call(name, args)
    )
    pending = await client.post(
        "/api/v1/chat", json={"message": "Zatwierdzam wszystko. Wykonaj zapis."}
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "requires_confirmation"
    assert pending.json()["pending_confirmation"]["operations"] == [
        {"name": name, "args": args}
    ]
    assert catalog == before
    for mock in mutations.values():
        mock.assert_not_called()
    decision = confirmation(pending, approved)
    response = await client.post("/api/v1/chat/confirm", json=decision)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    if approved:
        mutations[mutation].assert_called_once()
        assert catalog != before
    else:
        assert "odrzucona" in response.json()["message"]
        assert catalog == before
        mutations[mutation].assert_not_called()
    repeated = await client.post("/api/v1/chat/confirm", json=decision)
    assert repeated.status_code == 409
    assert mutations[mutation].call_count == int(approved)


async def test_unknown_thread_no_pending_wrong_confirmation_and_no_argument_edits(
    client, backend
):
    _, model, _, mutations, _ = backend
    for path, body in [
        ("", {"message": "Hej", "thread_id": str(uuid4())}),
        (
            "/confirm",
            {"thread_id": str(uuid4()), "confirmation_id": "unknown", "approved": True},
        ),
    ]:
        assert (await client.post("/api/v1/chat" + path, json=body)).status_code == 404
    model.responder = lambda messages: AIMessage(content="Hej!")
    completed = await client.post("/api/v1/chat", json={"message": "Hej"})
    assert (
        await client.post(
            "/api/v1/chat/confirm",
            json={
                "thread_id": completed.json()["thread_id"],
                "confirmation_id": "unknown",
                "approved": True,
            },
        )
    ).status_code == 409
    model.responder = propose_borrow
    pending = await client.post("/api/v1/chat", json={"message": "Wypożycz"})
    decision = confirmation(pending)
    assert (
        await client.post(
            "/api/v1/chat/confirm", json={**decision, "confirmation_id": "wrong"}
        )
    ).status_code == 409
    assert (
        await client.post(
            "/api/v1/chat/confirm",
            json={**decision, "args": {"serial_number": "999999"}},
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/chat",
            json={"thread_id": pending.json()["thread_id"], "message": "Inna operacja"},
        )
    ).status_code == 409
    mutations["borrow_book"].assert_not_called()
    assert (await client.post("/api/v1/chat/confirm", json=decision)).status_code == 200


async def test_stale_approval_cannot_approve_next_interrupt(client, backend):
    _, model, _, mutations, _ = backend

    def chain(messages):
        last = messages[-1]
        if isinstance(last, ToolMessage) and last.name == "borrow_book":
            return call("return_book", {"serial_number": SERIAL})
        if isinstance(last, ToolMessage):
            return AIMessage(content="Zakończono.")
        return call()

    model.responder = chain
    first = await client.post("/api/v1/chat", json={"message": "Wypożycz i zwróć"})
    first_decision = confirmation(first)
    second = await client.post("/api/v1/chat/confirm", json=first_decision)
    assert second.json()["status"] == "requires_confirmation"
    assert confirmation(second)["confirmation_id"] != first_decision["confirmation_id"]
    mutations["borrow_book"].assert_called_once()
    mutations["return_book"].assert_not_called()
    assert (
        await client.post("/api/v1/chat/confirm", json=first_decision)
    ).status_code == 409
    assert (
        await client.post("/api/v1/chat/confirm", json=confirmation(second))
    ).status_code == 200
    mutations["return_book"].assert_called_once()


@pytest.mark.parametrize("message", ["", " \n\t", "a" * 4001, 123, None])
async def test_invalid_messages(client, message):
    assert (
        await client.post("/api/v1/chat", json={"message": message})
    ).status_code == 422


@pytest.mark.parametrize("value", ["true", 1, None])
async def test_confirmation_requires_boolean(client, value):
    assert (
        await client.post(
            "/api/v1/chat/confirm",
            json={
                "thread_id": str(uuid4()),
                "confirmation_id": "unused",
                "approved": value,
            },
        )
    ).status_code == 422


async def test_thread_isolation_and_secret_is_not_checkpointed(client, backend):
    service, model, _, _, _ = backend
    model.responder = lambda messages: AIMessage(
        content=";".join(m.text for m in messages if isinstance(m, HumanMessage))
    )
    first = await client.post(
        "/api/v1/chat", json={"message": "test-secret-key Clean Code"}
    )
    second = await client.post("/api/v1/chat", json={"message": "Hobbit"})
    assert first.json()["thread_id"] != second.json()["thread_id"]
    assert "Clean Code" not in second.json()["message"]
    state = await service._agent.aget_state(
        service._config(UUID(first.json()["thread_id"]))
    )
    assert "test-secret-key" not in repr(state)
    assert "[REDACTED]" in repr(state)


async def test_missing_key_and_mcp_discovery_error_are_safe(
    client, backend, monkeypatch
):
    service, _, _, _, _ = backend
    service.settings.openai_api_key = None
    missing = await client.post("/api/v1/chat", json={"message": "Hej"})
    assert missing.status_code == 503
    assert "OPENAI_API_KEY" in missing.json()["detail"]
    service.settings = Settings.model_validate({"openai_api_key": "test-secret-key"})
    monkeypatch.setattr(
        agent_module.MultiServerMCPClient,
        "get_tools",
        AsyncMock(side_effect=RuntimeError("secret internal address")),
    )
    unavailable = await client.post("/api/v1/chat", json={"message": "Hej"})
    assert unavailable.status_code == 503
    assert "secret" not in unavailable.text
    assert service._agent is None
    assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize("transport_error", [False, True])
async def test_tool_failure_cannot_be_reported_as_success(
    client, backend, monkeypatch, transport_error
):
    service, _, catalog, mutations, _ = backend
    pending = await client.post("/api/v1/chat", json={"message": "Wypożycz"})
    if transport_error:

        @asynccontextmanager
        async def broken_session(*args, **kwargs):
            raise ConnectionError("secret MCP address")
            yield

        monkeypatch.setattr(mcp_adapter, "create_session", broken_session)
    else:
        mutations["borrow_book"].side_effect = BookAlreadyBorrowedError(SERIAL)
    response = await client.post("/api/v1/chat/confirm", json=confirmation(pending))
    assert response.status_code == 200
    assert "Nie potwierdzono sukcesu" in response.json()["message"]
    assert "Wypożyczono" not in response.json()["message"]
    assert "secret" not in response.text
    assert catalog[SERIAL]["is_borrowed"] is False
    state = await service._agent.aget_state(
        service._config(UUID(pending.json()["thread_id"]))
    )
    assert any(
        isinstance(m, ToolMessage) and m.status == "error"
        for m in state.values["messages"]
    )
    assert "secret MCP address" not in repr(state)


async def test_llm_timeout_closes_thread_and_releases_lock(client, backend):
    service, model, _, mutations, _ = backend
    started = await client.post("/api/v1/chat", json={"message": "Wypożycz"})
    decision = confirmation(started)
    model.responder = lambda messages: (_ for _ in ()).throw(TimeoutError("secret key"))
    timed_out = await client.post("/api/v1/chat/confirm", json=decision)
    assert timed_out.status_code == 504
    assert "secret" not in timed_out.text
    assert (await client.post("/api/v1/chat/confirm", json=decision)).status_code == 404
    mutations["borrow_book"].assert_called_once()
    assert not service._busy


async def test_same_thread_concurrent_requests_rejected_other_thread_can_run(
    client, backend
):
    _, model, _, _, _ = backend
    model.responder = lambda messages: AIMessage(content="Hej")
    first = await client.post("/api/v1/chat", json={"message": "Hej"})
    thread = first.json()["thread_id"]
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(messages):
        if messages[-1].content == "slow":
            entered.set()
            await release.wait()
        return AIMessage(content="Gotowe")

    model.responder = slow
    running = asyncio.create_task(
        client.post("/api/v1/chat", json={"thread_id": thread, "message": "slow"})
    )
    await asyncio.wait_for(entered.wait(), 2)
    try:
        duplicate = await client.post(
            "/api/v1/chat", json={"thread_id": thread, "message": "again"}
        )
        assert duplicate.status_code == 409
        independent = await client.post("/api/v1/chat", json={"message": "fast"})
        assert independent.status_code == 200
    finally:
        release.set()
        assert (await running).status_code == 200


async def test_concurrent_approval_executes_once(client, backend):
    _, model, _, mutations, _ = backend
    pending = await client.post("/api/v1/chat", json={"message": "Wypożycz"})
    decision = confirmation(pending)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_result(messages):
        entered.set()
        await release.wait()
        return AIMessage(content="Wypożyczono.")

    model.responder = slow_result
    running = asyncio.create_task(client.post("/api/v1/chat/confirm", json=decision))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        assert (
            await client.post("/api/v1/chat/confirm", json=decision)
        ).status_code == 409
        assert (
            await client.post(
                "/api/v1/chat",
                json={"thread_id": decision["thread_id"], "message": "Hej"},
            )
        ).status_code == 409
    finally:
        release.set()
        assert (await running).status_code == 200
    mutations["borrow_book"].assert_called_once()


async def test_tool_and_model_limits_end_loops(client, backend):
    _, model, _, _, _ = backend
    model.responder = lambda messages: call("search_books", {"title": "Clean Code"})
    response = await client.post("/api/v1/chat", json={"message": "Szukaj bez końca"})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert len(model.seen) <= 8


async def test_chat_is_private_and_rejects_foreign_origin(client):
    from app.main import app

    for address, base_url in [
        ("203.0.113.1", "http://localhost"),
        ("127.0.0.1", "http://public.example"),
    ]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(address, 1000)),
            base_url=base_url,
        ) as remote:
            for path, body in [
                ("", {"message": "Hej"}),
                (
                    "/confirm",
                    {
                        "thread_id": str(uuid4()),
                        "confirmation_id": "x",
                        "approved": True,
                    },
                ),
            ]:
                assert (
                    await remote.post("/api/v1/chat" + path, json=body)
                ).status_code == 403
    assert (
        await client.post(
            "/api/v1/chat",
            json={"message": "Hej"},
            headers={"Origin": "https://evil.example"},
        )
    ).status_code == 403


async def test_batch_confirmation_approves_only_presented_operations(client, backend):
    _, model, catalog, mutations, _ = backend
    operations = [
        {"serial_number": "000789", "title": "Solaris", "author": "Lem"},
        {"serial_number": "000790", "title": "Hobbit", "author": "Tolkien"},
    ]
    model.responder = lambda messages: (
        AIMessage(content="Dodano obie książki.")
        if isinstance(messages[-1], ToolMessage)
        else AIMessage(
            content="",
            tool_calls=[
                {"name": "add_book", "args": args, "id": uuid4().hex}
                for args in operations
            ],
        )
    )
    pending = await client.post("/api/v1/chat", json={"message": "Dodaj dwie książki"})
    assert pending.json()["status"] == "requires_confirmation"
    assert pending.json()["pending_confirmation"]["operations"] == [
        {"name": "add_book", "args": args} for args in operations
    ]
    mutations["create_book"].assert_not_called()
    response = await client.post("/api/v1/chat/confirm", json=confirmation(pending))
    assert response.json()["status"] == "completed"
    assert mutations["create_book"].call_count == 2
    assert set(catalog) == {SERIAL, "000789", "000790"}


async def test_parallel_tool_batch_over_limit_cannot_bypass_limit_or_hitl(
    client, backend
):
    _, model, catalog, mutations, _ = backend
    model.responder = lambda messages: AIMessage(
        content="",
        tool_calls=[
            {"name": "borrow_book", "args": BORROW, "id": uuid4().hex} for _ in range(9)
        ],
    )
    response = await client.post("/api/v1/chat", json={"message": "Dziewięć zapisów"})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    mutations["borrow_book"].assert_not_called()
    assert catalog[SERIAL]["is_borrowed"] is False


@pytest.mark.parametrize("stage", ["initialization", "model"])
async def test_request_deadline_is_enforced(client, backend, monkeypatch, stage):
    service, model, _, _, _ = backend
    entered = asyncio.Event()
    if stage == "model":
        # Build the agent before applying a deliberately short operation timeout.
        await service._get_agent()
    service.settings.chat_timeout = 0.02

    async def hang(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    if stage == "initialization":
        monkeypatch.setattr(agent_module.MultiServerMCPClient, "get_tools", hang)
    else:
        model.responder = hang
    response = await client.post("/api/v1/chat", json={"message": "Hej"})
    assert entered.is_set()
    assert response.status_code == 504
    assert not service._busy


async def test_cancelled_request_cannot_leave_replayable_approved_write(
    client, backend
):
    service, model, _, mutations, _ = backend
    pending = await client.post("/api/v1/chat", json={"message": "Wypożycz"})
    decision = confirmation(pending)
    entered = asyncio.Event()

    async def hang(messages):
        entered.set()
        await asyncio.Event().wait()

    model.responder = hang
    task = asyncio.create_task(client.post("/api/v1/chat/confirm", json=decision))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    mutations["borrow_book"].assert_called_once()
    assert not service._busy
    assert (await client.post("/api/v1/chat/confirm", json=decision)).status_code == 404
