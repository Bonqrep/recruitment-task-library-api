import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select
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
    monkeypatch.delenv("CHAT_ALLOWED_NETWORKS", raising=False)
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


@pytest.fixture
def mcp_url(database: Engine, tmp_path: Path) -> Iterator[str]:
    with database.connect() as connection:
        schema = connection.scalar(select(func.current_schema()))
    database_url = database.url.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    environment = {
        **os.environ,
        "DATABASE_URL": database_url.render_as_string(hide_password=False),
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
    }
    environment.pop("OPENAI_API_KEY", None)
    root = Path(__file__).resolve().parents[1]
    log_path = tmp_path / "mcp-server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.mcp.server"],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = monotonic() + 15
            while monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(f"MCP server exited: {log_path.read_text()}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    sleep(0.05)
            else:
                pytest.fail(f"MCP startup timed out: {log_path.read_text()}")
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
