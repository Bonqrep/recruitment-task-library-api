"""create books table

Revision ID: 0001
Revises:
Create Date: 2026-11-11 15:54:48.543943

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("serial_number", sa.String(length=6), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column(
            "is_borrowed", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("borrower_card_number", sa.String(length=6), nullable=True),
        sa.Column("borrowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "serial_number ~ '^[0-9]{6}$'",
            name="ck_books_serial_number_six_digits",
        ),
        sa.CheckConstraint(
            "borrower_card_number IS NULL OR borrower_card_number ~ '^[0-9]{6}$'",
            name="ck_books_borrower_card_number_six_digits",
        ),
        sa.CheckConstraint(
            "(is_borrowed = FALSE AND borrower_card_number IS NULL "
            "AND borrowed_at IS NULL) OR "
            "(is_borrowed = TRUE AND borrower_card_number IS NOT NULL "
            "AND borrowed_at IS NOT NULL)",
            name="ck_books_borrowing_state",
        ),
        sa.PrimaryKeyConstraint("serial_number"),
    )


def downgrade() -> None:
    op.drop_table("books")
