import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

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
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Migrate a separate schema per test; never modify existing application data."""
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests.")

    from app.db import session as session_module

    schema = f"test_books_{uuid4().hex}"
    admin = create_engine(database_url)
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        monkeypatch.setattr(session_module, "engine", engine)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
