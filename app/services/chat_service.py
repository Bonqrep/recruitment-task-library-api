"""Async chat orchestration; LangGraph checkpoints own all conversation state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.core.config import Settings, get_settings
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    PendingConfirmation,
    PendingOperation,
)

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.types import StateSnapshot

logger = logging.getLogger(__name__)


class ChatError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ChatService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._agent: CompiledStateGraph | None = None
        self._initializing = asyncio.Lock()
        self._busy: set[UUID] = set()

    @contextmanager
    def _exclusive(self, thread_id: UUID) -> Generator[None, None, None]:
        # No await between check and insertion: atomic on this process' event loop.
        if thread_id in self._busy:
            raise ChatError(409, "Rozmowa jest zajęta. Spróbuj ponownie później.")
        self._busy.add(thread_id)
        try:
            yield
        finally:
            self._busy.remove(thread_id)

    async def _get_agent(self) -> CompiledStateGraph:
        async with self._initializing:
            if self._agent is None:
                if not self.settings.openai_api_key or not (
                    self.settings.openai_api_key.get_secret_value()
                ):
                    raise ChatError(503, "Czat wymaga konfiguracji OPENAI_API_KEY.")
                from app.agent.agent import create_library_agent

                try:
                    async with asyncio.timeout(self.settings.chat_timeout):
                        self._agent = await create_library_agent(self.settings)
                except TimeoutError:
                    raise ChatError(
                        504, "Przekroczono czas uruchamiania czatu."
                    ) from None
                except Exception as exc:
                    logger.warning("Chat initialization failed: %s", type(exc).__name__)
                    raise ChatError(
                        503, "Czat lub serwer MCP jest niedostępny."
                    ) from None
            return self._agent

    @staticmethod
    def _config(thread_id: UUID) -> RunnableConfig:
        return {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 64}

    async def _existing(self, thread_id: UUID) -> StateSnapshot:
        if self._agent is None:
            raise ChatError(404, "Nie znaleziono rozmowy.")
        state = await self._agent.aget_state(self._config(thread_id))
        if state.created_at is None:
            raise ChatError(404, "Nie znaleziono rozmowy.")
        return state

    @staticmethod
    def _pending(state: StateSnapshot) -> PendingConfirmation | None:
        if not state.interrupts:
            return None
        if len(state.interrupts) != 1:
            raise ChatError(409, "Nie można jednoznacznie potwierdzić operacji.")
        interrupt = state.interrupts[0]
        actions = interrupt.value["action_requests"]
        return PendingConfirmation(
            confirmation_id=interrupt.id,
            operations=[
                PendingOperation(name=a["name"], args=a["args"]) for a in actions
            ],
        )

    def _redact(self, text: str) -> str:
        key = self.settings.openai_api_key
        if key and key.get_secret_value():
            return text.replace(key.get_secret_value(), "[REDACTED]")
        return text

    async def message(self, request: ChatRequest) -> ChatResponse:
        thread_id = request.thread_id or uuid4()
        with self._exclusive(thread_id):
            previous_count = 0
            if request.thread_id is not None:
                state = await self._existing(thread_id)
                if self._pending(state):
                    raise ChatError(409, "Rozmowa oczekuje na decyzję w /chat/confirm.")
                previous_count = len(state.values.get("messages", []))
            agent = await self._get_agent()
            return await self._invoke(
                agent,
                thread_id,
                {"messages": [("user", self._redact(request.message))]},
                previous_count,
            )

    async def confirm(self, request: ConfirmRequest) -> ChatResponse:
        with self._exclusive(request.thread_id):
            agent = self._agent
            if agent is None:
                raise ChatError(404, "Nie znaleziono rozmowy.")
            state = await self._existing(request.thread_id)
            pending = self._pending(state)
            if pending is None:
                raise ChatError(409, "Rozmowa nie oczekuje na zatwierdzenie operacji.")
            if pending.confirmation_id != request.confirmation_id:
                raise ChatError(
                    409, "Potwierdzenie dotyczy innej lub zakończonej operacji."
                )

            from langgraph.types import Command

            from app.agent.confirmation import approval_scope

            actions = [operation.model_dump() for operation in pending.operations]
            decision = {"type": "approve" if request.approved else "reject"}
            # Use the checkpoint's arguments, never arguments supplied by the client.
            with approval_scope(actions if request.approved else []):
                return await self._invoke(
                    agent,
                    request.thread_id,
                    Command(resume={"decisions": [dict(decision) for _ in actions]}),
                    len(state.values.get("messages", [])),
                    rejected=not request.approved,
                )

    async def _invoke(
        self,
        agent: CompiledStateGraph,
        thread_id: UUID,
        input_value: object,
        previous_count: int,
        *,
        rejected: bool = False,
    ) -> ChatResponse:
        from langchain_core.messages import AIMessage, ToolMessage
        from langgraph.errors import GraphRecursionError
        from openai import APITimeoutError

        try:
            async with asyncio.timeout(self.settings.chat_timeout):
                result = await agent.ainvoke(
                    input_value, config=self._config(thread_id)
                )
            state = await agent.aget_state(self._config(thread_id))
        except asyncio.CancelledError:
            await self._discard(agent, thread_id)
            raise
        except (TimeoutError, APITimeoutError):
            await self._discard(agent, thread_id)
            raise ChatError(
                504,
                "Przekroczono czas odpowiedzi. Rozmowa została zamknięta. "
                "Sprawdź stan katalogu przed ponowieniem operacji.",
            ) from None
        except Exception as exc:
            await self._discard(agent, thread_id)
            logger.warning("Chat invocation failed: %s", type(exc).__name__)
            detail = (
                "Osiągnięto limit kroków agenta. Rozpocznij nową rozmowę."
                if isinstance(exc, GraphRecursionError)
                else "Czat lub serwer MCP jest niedostępny. Rozmowa została zamknięta. "
                "Sprawdź stan katalogu przed ponowieniem operacji."
            )
            raise ChatError(503, detail) from None

        pending = self._pending(state)
        if pending:
            return ChatResponse(
                thread_id=thread_id,
                status="requires_confirmation",
                message="Operacja wymaga zatwierdzenia. Sprawdź wszystkie parametry.",
                pending_confirmation=pending,
            )
        messages = result["messages"]
        if rejected:
            message = "Operacja została odrzucona i nie została wykonana."
        elif any(
            isinstance(m, ToolMessage) and m.status == "error"
            for m in messages[previous_count:]
        ):
            # A model must never turn a failed business operation into a success reply.
            message = (
                "Narzędzie biblioteczne zgłosiło błąd. "
                "Nie potwierdzono sukcesu operacji."
            )
        elif isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
            message = messages[-1].text or "Zakończono przetwarzanie wiadomości."
        else:
            message = "Zakończono przetwarzanie wiadomości."
        return ChatResponse(
            thread_id=thread_id, status="completed", message=self._redact(message)
        )

    @staticmethod
    async def _discard(agent: CompiledStateGraph, thread_id: UUID) -> None:
        # A transport timeout can leave an uncertain write outcome. Never replay an
        # approved tool from a failed checkpoint; require checking the catalogue.
        checkpointer = agent.checkpointer
        if checkpointer is None or isinstance(checkpointer, bool):
            raise RuntimeError("Chat agent requires an explicit checkpoint saver.")
        await checkpointer.adelete_thread(str(thread_id))
