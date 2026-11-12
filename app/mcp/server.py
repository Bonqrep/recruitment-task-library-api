"""Run the standalone Streamable HTTP server: python -m app.mcp.server."""

import logging

from anyio import to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.db import session as db_session
from app.mcp.tools import register_tools


def create_server() -> FastMCP:
    settings = get_settings()
    server = FastMCP(
        "Library Management",
        host=settings.mcp_host,
        port=settings.mcp_port,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "mcp:8001"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
        instructions=(
            "Manage library books. Every write requires explicit user confirmation "
            "through MCP form elicitation. Clients must show the confirmation "
            "to the user and must never accept it automatically."
        ),
    )
    register_tools(server)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        def check_database() -> None:
            with db_session.engine.connect() as connection:
                connection.execute(select(1))

        try:
            await to_thread.run_sync(check_database)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    create_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
