"""Interactive CLI for the OpenAI-compatible Agent4Design adapter."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from agent4design.adapters.llm.openai_compatible import (
    create_openai_compatible_agent,
)
from agent4design.config import Agent4DesignSettings


class _CliApprovalController:
    """Allow write tools only when the CLI was launched in write mode."""

    def __init__(self, *, allow_writes: bool = False) -> None:
        self.allow_writes = allow_writes
        self.current_user_message = ""

    def set_user_message(self, message: str) -> None:
        self.current_user_message = message

    def __call__(self, name: str, arguments: Dict[str, Any]) -> bool:
        if self.allow_writes:
            print(f"\nWrite tool approved by --allow-writes: {name}", flush=True)
            return True
        print(
            f"\nWrite tool blocked: {name}. Restart with --allow-writes to write.",
            flush=True,
        )
        return False


def _print_delta(text: str) -> None:
    print(text, end="", flush=True)


def _print_status(message: str) -> None:
    print(f"\n[{message}]", flush=True)


def _auto_initialize(agent: Any) -> None:
    """Connect to Rhapsody and refresh known model types at CLI startup."""
    for tool_name, arguments in (
        ("initialize_rhapsody", {"select_current_target": True}),
        ("refresh_type_registry", {}),
    ):
        result = agent.service.call(tool_name, arguments)
        if result.success:
            _print_status(f"{tool_name} complete")
            continue
        _print_status(f"{tool_name} skipped: {result.error}")
        break


def main() -> None:
    """Run one prompt or an interactive Agent conversation."""
    parser = argparse.ArgumentParser(
        description="Run Agent4Design with an OpenAI-compatible model API."
    )
    parser.add_argument("--message", help="Run one user message and exit.")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Allow COM/XMI write tools for this CLI session.",
    )
    args = parser.parse_args()

    settings = Agent4DesignSettings.from_env()
    approval_controller = _CliApprovalController(allow_writes=args.allow_writes)
    agent = create_openai_compatible_agent(
        settings,
        approval_handler=approval_controller,
    )
    _auto_initialize(agent)
    messages = None

    if args.message:
        approval_controller.set_user_message(args.message)
        try:
            agent.run_stream(
                args.message,
                on_delta=_print_delta,
                on_status=_print_status,
            )
        except Exception as exc:
            print(f"\n[Agent error: {exc}]", flush=True)
        print()
        return

    mode = "write-enabled" if args.allow_writes else "read-only"
    print(f"Agent4Design Agent ready ({mode}). Type 'exit' to stop.")
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
        approval_controller.set_user_message(user_message)
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
            print(f"\n[Agent error: {exc}]", flush=True)


if __name__ == "__main__":
    main()
