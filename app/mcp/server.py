"""Run the standalone Streamable HTTP server: python -m app.mcp.server."""

import logging

from mcp.server.fastmcp import FastMCP

from app.core.config import get_settings
from app.mcp.tools import register_tools


def create_server() -> FastMCP:
    settings = get_settings()
    server = FastMCP(
        "Library Management",
        host=settings.mcp_host,
        port=settings.mcp_port,
        instructions=(
            "Manage library books. Every write requires explicit user confirmation "
            "through MCP form elicitation. Clients must show the confirmation "
            "to the user and must never accept it automatically."
        ),
    )
    register_tools(server)
    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    create_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
