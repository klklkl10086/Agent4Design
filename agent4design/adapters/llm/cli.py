"""Interactive CLI for the OpenAI-compatible Agent4Design adapter."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from agent4design.adapters.llm.openai_compatible import (
    create_openai_compatible_agent,
)
from agent4design.config import Agent4DesignSettings


def _approval_handler(name: str, arguments: Dict[str, Any]) -> bool:
    print(f"\nWrite tool requested: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Approve this Rhapsody write? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _print_delta(text: str) -> None:
    print(text, end="", flush=True)


def _print_status(message: str) -> None:
    print(f"\n[{message}]", file=sys.stderr, flush=True)


def main() -> None:
    """Run one prompt or an interactive Agent conversation."""
    parser = argparse.ArgumentParser(
        description="Run Agent4Design with an OpenAI-compatible model API."
    )
    parser.add_argument("--message", help="Run one user message and exit.")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Prompt for human approval when the model requests a write tool.",
    )
    args = parser.parse_args()

    settings = Agent4DesignSettings.from_env()
    agent = create_openai_compatible_agent(
        settings,
        approval_handler=_approval_handler if args.allow_writes else None,
    )
    messages = None

    if args.message:
        try:
            agent.run_stream(
                args.message,
                on_delta=_print_delta,
                on_status=_print_status,
            )
        except Exception as exc:
            print(f"\n[Agent error: {exc}]", file=sys.stderr, flush=True)
        print()
        return

    print("Agent4Design Agent ready. Type 'exit' to stop.")
    while True:
        try:
            user_message = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            return
        try:
            print("\nAgent> ", end="", flush=True)
            result = agent.run_stream(
                user_message,
                messages=messages,
                on_delta=_print_delta,
                on_status=_print_status,
            )
            messages = result.messages
            print()
        except Exception as exc:
            print(f"\n[Agent error: {exc}]", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
