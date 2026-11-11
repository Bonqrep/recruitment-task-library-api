from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def configure_test_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://library@localhost:5432/library"
    )
    monkeypatch.delenv("APP_NAME", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
