from io import StringIO
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config


def test_session_factory() -> None:
    from app.db.session import SessionLocal, engine

    with SessionLocal() as first, SessionLocal() as second:
        assert first is not second
        assert first.get_bind() is engine
        assert first.expire_on_commit is False


def test_alembic_offline() -> None:
    output = StringIO()
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path), output_buffer=output)

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "BEGIN" in sql
    assert "COMMIT" in sql
