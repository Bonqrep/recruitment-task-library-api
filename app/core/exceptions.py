"""Domain errors independent of the HTTP transport."""


class BookNotFoundError(Exception):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"Book '{serial_number}' not found.")


class BookAlreadyExistsError(Exception):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"Book '{serial_number}' already exists.")


class BookBorrowedError(Exception):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"Borrowed book '{serial_number}' cannot be deleted.")


class BookAlreadyBorrowedError(Exception):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"Book '{serial_number}' is already borrowed.")


class BookNotBorrowedError(Exception):
    def __init__(self, serial_number: str) -> None:
        super().__init__(f"Book '{serial_number}' is not borrowed.")
