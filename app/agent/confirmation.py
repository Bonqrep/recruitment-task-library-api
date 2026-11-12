"""Bridge checkpoint approval to the existing MCP form elicitation."""

import json
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from langchain_mcp_adapters.callbacks import CallbackContext, ElicitRequestParams
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langgraph.types import Command
from mcp.shared.context import RequestContext
from mcp.types import CallToolResult, ElicitResult

# The adapter defines this union conditionally, without an explicit type alias.
# LangGraph is a required dependency here, so declare its full result type.
type MCPToolCallResult = CallToolResult | ToolMessage | Command

WRITE_TOOL_NAMES = frozenset({"add_book", "borrow_book", "return_book", "delete_book"})


@dataclass
class _Grant:
    name: str
    args: dict[str, Any]
    used: bool = False
    elicited: bool = False


_grants: ContextVar[tuple[_Grant, ...]] = ContextVar("mcp_approval_grants", default=())
_current_grant: ContextVar[_Grant | None] = ContextVar(
    "mcp_current_grant", default=None
)


@contextmanager
def approval_scope(actions: list[dict[str, Any]]) -> Generator[None, None, None]:
    token = _grants.set(tuple(_Grant(a["name"], deepcopy(a["args"])) for a in actions))
    try:
        yield
    finally:
        _grants.reset(token)


async def guard_mcp_call(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
) -> MCPToolCallResult:
    if request.name not in WRITE_TOOL_NAMES:
        return await handler(request)
    grant = next(
        (
            g
            for g in _grants.get()
            if not g.used and g.name == request.name and g.args == request.args
        ),
        None,
    )
    if request.server_name != "library" or grant is None:
        raise ToolException("This exact operation has not been approved.")
    grant.used = True
    token = _current_grant.set(grant)
    try:
        return await handler(request)
    finally:
        _current_grant.reset(token)


async def approved_elicitation(
    mcp_context: RequestContext,
    params: ElicitRequestParams,
    context: CallbackContext,
) -> ElicitResult:
    grant = _current_grant.get()
    if grant is None or grant.elicited:
        return ElicitResult(action="decline")
    expected = (
        f"Approve library operation '{grant.name}' with these exact parameters?\n"
        + json.dumps(grant.args, ensure_ascii=False, sort_keys=True)
    )
    schema = getattr(params, "requestedSchema", {})
    if (
        context.server_name != "library"
        or context.tool_name != grant.name
        or getattr(params, "mode", "form") != "form"
        or params.message != expected
        or schema.get("required") != ["confirmed"]
        or schema.get("properties", {}).get("confirmed", {}).get("type") != "boolean"
    ):
        return ElicitResult(action="decline")
    grant.elicited = True
    return ElicitResult(action="accept", content={"confirmed": True})
