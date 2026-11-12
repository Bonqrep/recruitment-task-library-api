import subprocess
import sys
from unittest.mock import ANY, AsyncMock, Mock

import pytest
from langchain_core.tools import StructuredTool
from pydantic import SecretStr

from app.agent import agent as agent_module
from app.core.config import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("key", [None, ""])
async def test_missing_key_fails_only_when_ai_is_used(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    model = Mock()
    client = Mock()
    monkeypatch.setattr(agent_module, "ChatOpenAI", model)
    monkeypatch.setattr(agent_module, "MultiServerMCPClient", client)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        await agent_module.create_library_agent(
            Settings.model_validate({"openai_api_key": key})
        )

    model.assert_not_called()
    client.assert_not_called()


def test_api_starts_without_importing_ai_or_creating_openai_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from fastapi.testclient import TestClient; "
            "from app.main import app; "
            "assert 'langchain_openai' not in sys.modules; "
            "assert 'openai' not in sys.modules; "
            "assert TestClient(app).get('/health').json() == {'status': 'ok'}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("model_name", "temperature"),
    [
        ("gpt-4.1-mini", 0),
        ("o3-mini", None),
        ("o1", None),
        ("gpt-5", None),
        ("gpt-5.1", None),
        ("unknown-model", None),
    ],
)
def test_model_uses_config_and_only_supported_temperature(
    model_name: str, temperature: float | None
) -> None:
    settings = Settings.model_validate(
        {
            "openai_api_key": "test-key",
            "openai_model": model_name,
            "openai_timeout": 12.5,
        }
    )
    model = agent_module._create_model(settings)
    assert model.model_name == model_name
    assert model.request_timeout == 12.5
    assert model.temperature == temperature
    assert isinstance(model.openai_api_key, SecretStr)
    assert model.openai_api_key.get_secret_value() == "test-key"


def make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda: "unused", name=name, description=name
    )


async def test_factory_exposes_only_allowlisted_mcp_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovered = [
        make_tool(name)
        for name in (
            "search_books",
            "get_book",
            "borrow_book",
            "add_book",
            "return_book",
            "delete_book",
            "future_write_tool",
        )
    ]
    client = Mock(get_tools=AsyncMock(return_value=discovered))
    client_factory = Mock(return_value=client)
    model = Mock()
    build = Mock()
    monkeypatch.setattr(agent_module, "MultiServerMCPClient", client_factory)
    monkeypatch.setattr(agent_module, "_create_model", Mock(return_value=model))
    monkeypatch.setattr(agent_module, "create_agent", build)
    settings = Settings.model_validate({"mcp_server_url": "http://localhost:9876/mcp"})

    result = await agent_module.create_library_agent(settings)

    client_factory.assert_called_once_with(
        {
            "library": {
                "transport": "streamable_http",
                "url": "http://localhost:9876/mcp",
            }
        },
        callbacks=ANY,
        tool_interceptors=[agent_module.guard_mcp_call],
    )
    client.get_tools.assert_awaited_once_with()
    build.assert_called_once_with(
        model=model,
        tools=discovered[:6],
        system_prompt=agent_module.SYSTEM_PROMPT,
        checkpointer=ANY,
        middleware=ANY,
    )
    assert result is build.return_value


async def test_factory_rejects_incomplete_mcp_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_module, "_create_model", Mock())
    client = Mock(get_tools=AsyncMock(return_value=[make_tool("search_books")]))
    monkeypatch.setattr(agent_module, "MultiServerMCPClient", Mock(return_value=client))
    build = Mock()
    monkeypatch.setattr(agent_module, "create_agent", build)
    with pytest.raises(RuntimeError, match="get_book"):
        await agent_module.create_library_agent(Settings.model_validate({}))
    build.assert_not_called()


def test_local_chat_without_key_uses_lifespan_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(
        app, base_url="http://localhost", client=("127.0.0.1", 1234)
    ) as client:
        response = client.post("/api/v1/chat", json={"message": "Hej"})
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]
        assert client.get("/health").status_code == 200
