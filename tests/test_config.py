from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


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

    settings = Settings()

    assert str(settings.database_url) == database_url
    assert settings.app_name == "Library Management API"
