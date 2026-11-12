"""Lazy construction of a read-only LangChain agent backed by the MCP server."""

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from app.agent.prompts import SYSTEM_PROMPT
from app.core.config import Settings, get_settings

READ_ONLY_TOOL_NAMES = frozenset({"search_books", "get_book"})


def _create_model(settings: Settings) -> ChatOpenAI:
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        raise ValueError("Set OPENAI_API_KEY before using the AI agent.")

    model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout,
        temperature=0,
    )
    if (model.profile or {}).get("temperature") is not True:
        model.temperature = None
    return model


async def create_library_agent(settings: Settings | None = None) -> CompiledStateGraph:
    """Discover MCP tools and create one agent, only when AI is explicitly used.
    """
    settings = settings or get_settings()
    model = _create_model(settings)
    client = MultiServerMCPClient(
        {
            "library": {
                "transport": "streamable_http",
                "url": str(settings.mcp_server_url),
            }
        }
    )
    tools = [
        tool for tool in await client.get_tools() if tool.name in READ_ONLY_TOOL_NAMES
    ]
    if {tool.name for tool in tools} != READ_ONLY_TOOL_NAMES:
        raise RuntimeError("MCP server must expose search_books and get_book.")
    for tool in tools:
        tool.handle_tool_error = True

    return create_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
