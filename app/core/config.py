from functools import lru_cache
from typing import Annotated

from pydantic import PostgresDsn, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: Annotated[
        PostgresDsn, UrlConstraints(allowed_schemes=["postgresql+psycopg"])
    ]
    app_name: str = "Library Management API"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
