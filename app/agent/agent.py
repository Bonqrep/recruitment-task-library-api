"""Lazy construction of a checkpointed LangChain agent backed by MCP."""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain_mcp_adapters.callbacks import Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.confirmation import (
    WRITE_TOOL_NAMES,
    approved_elicitation,
    guard_mcp_call,
)
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
        max_retries=0,
        temperature=0,
    )
    if (model.profile or {}).get("temperature") is not True:
        model.temperature = None
    return model


async def create_library_agent(settings: Settings | None = None) -> CompiledStateGraph:
    """Discover MCP tools and create one agent, only when AI is explicitly used."""
    settings = settings or get_settings()
    model = _create_model(settings)
    client = MultiServerMCPClient(
        {
            "library": {
                "transport": "streamable_http",
                "url": str(settings.mcp_server_url),
            }
        },
        callbacks=Callbacks(on_elicitation=approved_elicitation),
        tool_interceptors=[guard_mcp_call],
    )
    tools = [
        tool
        for tool in await client.get_tools()
        if tool.name in READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES
    ]
    if {tool.name for tool in tools} != READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES:
        raise RuntimeError(
            "MCP server must expose search_books, get_book, add_book, "
            "borrow_book, return_book and delete_book."
        )
    for tool in tools:
        # The middleware sanitizes failures before they reach the model/checkpoint.
        tool.handle_tool_error = False

    # LangChain merges the different middleware state schemas when building the graph.
    middleware: list[AgentMiddleware[Any, None]] = [
        HumanInTheLoopMiddleware(
            interrupt_on={
                name: {"allowed_decisions": ["approve", "reject"]}
                for name in WRITE_TOOL_NAMES
            },
            interrupt_mode="batched",
        ),
        ToolCallLimitMiddleware(run_limit=8, thread_limit=50, exit_behavior="end"),
        ModelCallLimitMiddleware(run_limit=8, thread_limit=100, exit_behavior="end"),
        ToolErrorMiddleware(
            lambda exc, request: "Library tool failed. No success confirmed."
        ),
    ]

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
        middleware=middleware,
    )
