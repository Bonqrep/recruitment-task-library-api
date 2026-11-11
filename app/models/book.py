from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint(
            "serial_number ~ '^[0-9]{6}$'",
            name="ck_books_serial_number_six_digits",
        ),
        CheckConstraint(
            "borrower_card_number IS NULL OR borrower_card_number ~ '^[0-9]{6}$'",
            name="ck_books_borrower_card_number_six_digits",
        ),
        CheckConstraint(
            "(is_borrowed = FALSE AND borrower_card_number IS NULL "
            "AND borrowed_at IS NULL) OR "
            "(is_borrowed = TRUE AND borrower_card_number IS NOT NULL "
            "AND borrowed_at IS NOT NULL)",
            name="ck_books_borrowing_state",
        ),
    )

    serial_number: Mapped[str] = mapped_column(String(6), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    is_borrowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    borrower_card_number: Mapped[str | None] = mapped_column(String(6), nullable=True)
    # PostgreSQL stores TIMESTAMP WITH TIME ZONE instants in UTC.
    borrowed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
