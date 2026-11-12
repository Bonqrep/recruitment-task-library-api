from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.types import BookText, SixDigitIdentifier


class BookBase(BaseModel):
    serial_number: SixDigitIdentifier = Field(
        examples=["000001"], description="Sześciocyfrowy numer seryjny."
    )
    title: BookText = Field(examples=["Solaris"])
    author: BookText = Field(examples=["Stanisław Lem"])


class BookCreate(BookBase):
    model_config = ConfigDict(extra="forbid")


class BookRead(BookBase):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "serial_number": "000001",
                    "title": "Solaris",
                    "author": "Stanisław Lem",
                    "is_borrowed": True,
                    "borrower_card_number": "000456",
                    "borrowed_at": "2026-11-12T12:00:00Z",
                }
            ]
        },
    )

    is_borrowed: bool
    borrower_card_number: SixDigitIdentifier | None
    borrowed_at: AwareDatetime | None


class BookBorrow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_card_number: SixDigitIdentifier = Field(
        examples=["000456"], description="Sześciocyfrowy numer karty czytelnika."
    )
