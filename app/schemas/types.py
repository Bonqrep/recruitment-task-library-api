from typing import Annotated

from pydantic import StringConstraints

SixDigitIdentifier = Annotated[
    str,
    StringConstraints(strict=True, min_length=6, max_length=6, pattern=r"^[0-9]{6}$"),
]

BookText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
