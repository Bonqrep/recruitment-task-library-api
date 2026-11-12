"""Public chat contracts. Operation arguments cannot be edited on confirmation."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

MessageText = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=4000
    ),
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    message: MessageText


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    confirmation_id: str = Field(min_length=1, max_length=128)
    approved: StrictBool


class PendingOperation(BaseModel):
    name: Literal["add_book", "borrow_book", "return_book", "delete_book"]
    args: dict[str, Any]


class PendingConfirmation(BaseModel):
    confirmation_id: str
    operations: list[PendingOperation]


class ChatResponse(BaseModel):
    thread_id: UUID
    status: Literal["completed", "requires_confirmation"]
    message: str
    pending_confirmation: PendingConfirmation | None = None
