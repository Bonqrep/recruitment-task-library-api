from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import Book
from app.schemas.book import BookBorrow, BookCreate, BookRead


def test_create_trims_text_and_preserves_leading_zeroes() -> None:
    book = BookCreate(serial_number="000123", title="  Solaris\t", author="\n Lem ")

    assert book.model_dump() == {
        "serial_number": "000123",
        "title": "Solaris",
        "author": "Lem",
    }
    assert BookBorrow(borrower_card_number="000456").borrower_card_number == "000456"


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "12345",
        "1234567",
        "12345a",
        "１２３４５６",
        "١٢٣٤٥٦",
        " 123456",
        "123456 ",
        "123456\n",
        123456,
        b"123456",
        None,
    ],
)
@pytest.mark.parametrize("schema", [BookCreate, BookBorrow])
def test_requests_reject_invalid_identifiers(schema: type, identifier: object) -> None:
    if schema is BookCreate:
        data = {"serial_number": identifier, "title": "Solaris", "author": "Lem"}
    else:
        data = {"borrower_card_number": identifier}

    with pytest.raises(ValidationError):
        schema.model_validate(data)


@pytest.mark.parametrize("field", ["title", "author"])
@pytest.mark.parametrize("value", ["", " \t\n", "a" * 256, "a\x00b", None, 123])
def test_create_rejects_invalid_text(field: str, value: object) -> None:
    data = {"serial_number": "000123", "title": "Solaris", "author": "Lem"}
    data[field] = value

    with pytest.raises(ValidationError):
        BookCreate.model_validate(data)


@pytest.mark.parametrize("length", [1, 255])
def test_text_length_is_checked_after_trimming(length: int) -> None:
    text = "a" * length

    book = BookCreate(serial_number="000123", title=f"  {text}\n", author=f"\t{text} ")

    assert book.title == text
    assert book.author == text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_borrowed", True),
        ("borrower_card_number", "000456"),
        ("borrowed_at", "2026-11-11T12:00:00Z"),
        ("unknown", "value"),
    ],
)
def test_create_forbids_additional_fields(field: str, value: object) -> None:
    data = {"serial_number": "000123", "title": "Solaris", "author": "Lem"}
    data[field] = value

    with pytest.raises(ValidationError, match="extra_forbidden"):
        BookCreate.model_validate(data)


@pytest.mark.parametrize("field", ["serial_number", "is_borrowed", "borrowed_at"])
def test_borrow_forbids_additional_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BookBorrow.model_validate({"borrower_card_number": "000456", field: "value"})


@pytest.mark.parametrize("schema", [BookCreate, BookBorrow])
def test_requests_require_their_fields(schema: type) -> None:
    with pytest.raises(ValidationError, match="missing"):
        schema.model_validate({})


@pytest.mark.parametrize("is_borrowed", [False, True])
def test_read_from_orm_attributes(is_borrowed: bool) -> None:
    borrowed_at = datetime(2026, 11, 11, 12, tzinfo=UTC) if is_borrowed else None
    book = Book(
        serial_number="000123",
        title="Solaris",
        author="Lem",
        is_borrowed=is_borrowed,
        borrower_card_number="000456" if is_borrowed else None,
        borrowed_at=borrowed_at,
    )

    result = BookRead.model_validate(book)

    assert result.serial_number == "000123"
    assert result.title == "Solaris"
    assert result.author == "Lem"
    assert result.is_borrowed is is_borrowed
    assert result.borrower_card_number == book.borrower_card_number
    assert result.borrowed_at == borrowed_at
    if is_borrowed:
        assert result.model_dump(mode="json")["borrowed_at"] == "2026-11-11T12:00:00Z"


def test_read_rejects_timestamp_without_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone_aware"):
        BookRead(
            serial_number="000123",
            title="Solaris",
            author="Lem",
            is_borrowed=True,
            borrower_card_number="000456",
            borrowed_at=datetime(2026, 11, 11, 12),
        )
