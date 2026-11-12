from io import StringIO
from pathlib import Path
from unittest.mock import patch

from alembic import command, context
from alembic.config import Config

from app.db.base import Base


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

    with patch("alembic.context.configure", wraps=context.configure) as configure:
        command.upgrade(config, "head", sql=True)

    assert configure.call_args.kwargs["target_metadata"] is Base.metadata
    assert set(Base.metadata.tables) == {"books"}

    sql = output.getvalue()
    assert "BEGIN" in sql
    assert "COMMIT" in sql
    assert "CREATE TABLE books" in sql
    assert "serial_number VARCHAR(6) NOT NULL" in sql
    assert "PRIMARY KEY (serial_number)" in sql
    assert "title VARCHAR(255) NOT NULL" in sql
    assert "author VARCHAR(255) NOT NULL" in sql
    assert "is_borrowed BOOLEAN DEFAULT false NOT NULL" in sql
    assert "borrower_card_number VARCHAR(6)" in sql
    assert "borrowed_at TIMESTAMP WITH TIME ZONE" in sql
    assert "ck_books_serial_number_six_digits CHECK" in sql
    assert "ck_books_borrower_card_number_six_digits CHECK" in sql
    assert "ck_books_borrowing_state CHECK" in sql


def test_alembic_offline_downgrade() -> None:
    output = StringIO()
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path), output_buffer=output)

    command.downgrade(config, "head:base", sql=True)

    assert "DROP TABLE books" in output.getvalue()
