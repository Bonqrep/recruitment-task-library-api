from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        Settings.model_validate({})


def test_settings_read_environment_and_are_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+psycopg://test@localhost/test"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_NAME", "Test Library")

    settings = get_settings()

    assert str(settings.database_url) == database_url
    assert settings.app_name == "Test Library"
    assert get_settings() is settings


def test_settings_read_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL")
    database_url = "postgresql+psycopg://local@localhost/library"
    (tmp_path / ".env").write_text(
        f"DATABASE_URL={database_url}\n",
        encoding="utf-8",
    )

    settings = Settings.model_validate({})

    assert str(settings.database_url) == database_url
    assert settings.app_name == "Library Management API"


def test_mcp_settings_defaults_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_validate({})
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8001

    monkeypatch.setenv("MCP_HOST", "localhost")
    monkeypatch.setenv("MCP_PORT", "9001")
    settings = Settings.model_validate({})
    assert settings.mcp_host == "localhost"
    assert settings.mcp_port == 9001


@pytest.mark.parametrize("port", ["0", "65536", "abc"])
def test_mcp_settings_reject_invalid_ports(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("MCP_PORT", port)
    with pytest.raises(ValidationError, match="mcp_port"):
        Settings.model_validate({})


def test_ai_settings_are_optional_and_hide_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert Settings.model_validate({}).openai_api_key is None
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-value")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_TIMEOUT", "12.5")
    monkeypatch.setenv("MCP_SERVER_URL", "http://localhost:9001/mcp")
    settings = Settings.model_validate({})
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-secret-value"
    assert "test-secret-value" not in repr(settings)
    assert settings.openai_model == "configured-model"
    assert settings.openai_timeout == 12.5
    assert str(settings.mcp_server_url) == "http://localhost:9001/mcp"


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "abc"])
def test_ai_timeout_must_be_positive_and_finite(
    monkeypatch: pytest.MonkeyPatch, timeout: str
) -> None:
    monkeypatch.setenv("OPENAI_TIMEOUT", timeout)
    with pytest.raises(ValidationError, match="openai_timeout"):
        Settings.model_validate({})


def test_mcp_server_url_requires_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SERVER_URL", "file:///tmp/mcp")
    with pytest.raises(ValidationError, match="mcp_server_url"):
        Settings.model_validate({})
