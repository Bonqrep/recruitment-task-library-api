"""Developer CLI: python -m app.agent.demo [question]. No conversation memory."""

import argparse
import asyncio
import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.agent import create_library_agent

SCENARIOS = (
    "Pokaż wszystkie książki",
    "Znajdź książki Tolkiena",
    "Czy Clean Code jest dostępna?",
    "Pokaż wypożyczone książki",
    "Znajdź książkę o numerze 123456",
)


async def run(questions: list[str]) -> None:
    agent = await create_library_agent()
    for question in questions:
        print(f"\nUSER: {question}")
        result = await agent.ainvoke(
            {"messages": [("user", question)]}, config={"recursion_limit": 50}
        )
        for message in result["messages"]:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    print(
                        f"CALL {call['name']}: "
                        f"{json.dumps(call['args'], ensure_ascii=False)}"
                    )
            elif isinstance(message, ToolMessage):
                print(f"MCP {message.name} [{message.status}]: {message.content}")
        print(f"ASSISTANT: {result['messages'][-1].text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", help="Omit to run all five scenarios.")
    args = parser.parse_args()
    try:
        asyncio.run(run([args.question] if args.question else list(SCENARIOS)))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
