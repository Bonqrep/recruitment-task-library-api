from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network
from typing import Annotated

from pydantic import Field, HttpUrl, PostgresDsn, SecretStr, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: Annotated[
        PostgresDsn, UrlConstraints(allowed_schemes=["postgresql+psycopg"])
    ]
    app_name: str = "Library Management API"
    mcp_host: str = Field(default="127.0.0.1", min_length=1)
    mcp_port: int = Field(default=8001, ge=1, le=65535)
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-4.1-mini", min_length=1)
    openai_timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    chat_timeout: float = Field(default=90.0, gt=0, allow_inf_nan=False)
    mcp_server_url: HttpUrl = HttpUrl("http://127.0.0.1:8001/mcp")
    chat_allowed_networks: list[IPv4Network | IPv6Network] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
