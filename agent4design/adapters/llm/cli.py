"""Interactive CLI for the OpenAI-compatible Agent4Design adapter."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict

from agent4design.adapters.llm.openai_compatible import (
    create_openai_compatible_agent,
)
from agent4design.config import Agent4DesignSettings


_APPROVAL_PATTERNS = (
    "\u6279\u51c6\u6267\u884c",
    "\u6279\u51c6",
    "\u540c\u610f\u6267\u884c",
    "\u540c\u610f",
    "\u6388\u6743\u6267\u884c",
    "\u6388\u6743",
    "\u786e\u8ba4\u6267\u884c",
    "\u786e\u8ba4",
    "\u5141\u8bb8\u6267\u884c",
    "\u5141\u8bb8\u5199\u5165",
    "\u5141\u8bb8",
    "\u7acb\u5373\u6267\u884c",
    "\u6267\u884c\u540c\u6b65\u4e0d\u9700\u8981\u8be2\u95ee",
)
_DENIAL_PATTERNS = (
    "\u4e0d\u6279\u51c6",
    "\u4e0d\u540c\u610f",
    "\u4e0d\u6388\u6743",
    "\u4e0d\u5141\u8bb8",
    "\u4e0d\u8981\u6267\u884c",
    "\u522b\u6267\u884c",
    "\u62d2\u7edd",
    "\u53d6\u6d88",
)
_APPROVAL_WORDS = {
    "y",
    "yes",
    "ok",
    "okay",
    "approve",
    "approved",
    "proceed",
    "execute",
    "run",
    "continue",
}
_DENIAL_WORDS = {"deny", "reject", "cancel", "no", "not"}


def _compact_approval_text(message: str) -> str:
    return re.sub(r"[\s\"'`*_.,;:!?()\[\]{}<>，。；：！？（）【】《》]+", "", message.casefold())


def _approval_words(message: str) -> set[str]:
    return set(re.findall(r"[a-z]+", message.casefold()))


def _message_grants_write_approval(message: str) -> bool:
    """Return true when the latest human message explicitly approves a write."""
    compact = _compact_approval_text(message)
    if not compact:
        return False
    if _message_denies_write_approval(message):
        return False
    if compact in _APPROVAL_WORDS or _approval_words(message) & _APPROVAL_WORDS:
        return True
    return any(pattern in compact for pattern in _APPROVAL_PATTERNS)


def _message_denies_write_approval(message: str) -> bool:
    """Return true when the latest human message explicitly rejects a write."""
    compact = _compact_approval_text(message)
    if not compact:
        return False
    return (
        any(pattern in compact for pattern in _DENIAL_PATTERNS)
        or bool(_approval_words(message) & _DENIAL_WORDS)
    )


def _approval_handler(name: str, arguments: Dict[str, Any]) -> bool:
    print(f"\nWrite tool requested: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    try:
        answer = input("Approve this Rhapsody write? [y/N] ").strip().lower()
    except EOFError:
        print(
            "No terminal input is available for write approval. "
            "Re-run with --allow-writes to approve this CLI session, or use "
            "--deny-writes for read-only mode.",
            file=sys.stderr,
            flush=True,
        )
        return False
    return answer in {"y", "yes"}


class _CliApprovalController:
    """Bridge explicit chat approvals into the local write-tool gate."""

    def __init__(
        self,
        *,
        prompt_for_writes: bool = True,
        auto_approve_writes: bool = False,
    ) -> None:
        self.prompt_for_writes = prompt_for_writes
        self.auto_approve_writes = auto_approve_writes
        self.current_user_message = ""

    def set_user_message(self, message: str) -> None:
        self.current_user_message = message

    def __call__(self, name: str, arguments: Dict[str, Any]) -> bool:
        if _message_denies_write_approval(self.current_user_message):
            return False
        if self.auto_approve_writes:
            print(
                f"\nWrite tool approved by --allow-writes: {name}",
                file=sys.stderr,
                flush=True,
            )
            return True
        if _message_grants_write_approval(self.current_user_message):
            print(
                f"\nWrite tool approved by latest user message: {name}",
                file=sys.stderr,
                flush=True,
            )
            return True
        if self.prompt_for_writes:
            return _approval_handler(name, arguments)
        return False


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
    approval_group = parser.add_mutually_exclusive_group()
    approval_group.add_argument(
        "--allow-writes",
        action="store_true",
        help="Approve write tools for this CLI session.",
    )
    approval_group.add_argument(
        "--deny-writes",
        action="store_true",
        help="Disable interactive write approval prompts and deny write tools.",
    )
    args = parser.parse_args()

    settings = Agent4DesignSettings.from_env()
    approval_controller = _CliApprovalController(
        prompt_for_writes=not args.deny_writes,
        auto_approve_writes=args.allow_writes,
    )
    agent = create_openai_compatible_agent(
        settings,
        approval_handler=approval_controller,
    )
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
            print(f"\n[Agent error: {exc}]", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
