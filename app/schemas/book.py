from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.types import BookText, SixDigitIdentifier


class BookBase(BaseModel):
    serial_number: SixDigitIdentifier
    title: BookText
    author: BookText


class BookCreate(BookBase):
    model_config = ConfigDict(extra="forbid")


class BookRead(BookBase):
    model_config = ConfigDict(from_attributes=True)

    is_borrowed: bool
    borrower_card_number: SixDigitIdentifier | None
    borrowed_at: AwareDatetime | None


class BookBorrow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_card_number: SixDigitIdentifier
