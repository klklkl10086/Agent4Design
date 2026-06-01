"""Offline tests for the OpenAI-compatible Agent adapter."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from agent4design.adapters.llm.openai_compatible import OpenAICompatibleAgent
from agent4design.services.agent_service import (
    AgentToolDefinition,
    AgentToolResult,
)


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _tool_call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeCompletions:
    def __init__(self, messages):
        self.messages = list(messages)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=self.messages.pop(0))]
        )


class FakeClient:
    def __init__(self, messages):
        self.chat = SimpleNamespace(completions=FakeCompletions(messages))


class FakeService:
    def __init__(self):
        self.calls = []

    @staticmethod
    def list_tools():
        return [
            AgentToolDefinition(
                name="get_rhapsody_context",
                description="Read context.",
                input_schema={"type": "object", "properties": {}},
            ),
            AgentToolDefinition(
                name="execute_agent4design_sync",
                description="Write approved changes.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "request": {"type": "object"},
                        "approved": {"type": "boolean"},
                    },
                    "required": ["request", "approved"],
                },
            ),
        ]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return AgentToolResult(name=name, success=True, output=arguments)


class OpenAICompatibleAgentTests(unittest.TestCase):
    def test_runs_tool_loop_and_returns_final_response(self):
        client = FakeClient(
            [
                _message(tool_calls=[_tool_call("get_rhapsody_context", "{}")]),
                _message("Selected target is PackageA."),
            ]
        )
        service = FakeService()
        result = OpenAICompatibleAgent(service, client, model="test-model").run(
            "Inspect the target."
        )

        self.assertEqual(result.response, "Selected target is PackageA.")
        self.assertEqual(service.calls, [("get_rhapsody_context", {})])
        self.assertEqual(result.tool_calls[0].name, "get_rhapsody_context")
        self.assertEqual(
            client.chat.completions.requests[1]["messages"][-1]["role"],
            "tool",
        )

    def test_model_cannot_approve_its_own_write(self):
        client = FakeClient(
            [
                _message(
                    tool_calls=[
                        _tool_call(
                            "execute_agent4design_sync",
                            '{"request": {}, "approved": true}',
                        )
                    ]
                ),
                _message("Write was denied."),
            ]
        )
        service = FakeService()
        agent = OpenAICompatibleAgent(service, client, model="test-model")
        result = agent.run("Write the model.")

        self.assertEqual(service.calls, [])
        self.assertFalse(result.tool_calls[0].result.success)
        self.assertFalse(result.tool_calls[0].human_approved)

    def test_human_approval_is_injected_after_confirmation(self):
        client = FakeClient(
            [
                _message(
                    tool_calls=[
                        _tool_call(
                            "execute_agent4design_sync",
                            '{"request": {}, "approved": false}',
                        )
                    ]
                ),
                _message("Write completed."),
            ]
        )
        service = FakeService()
        agent = OpenAICompatibleAgent(
            service,
            client,
            model="test-model",
            approval_handler=lambda name, arguments: True,
        )
        result = agent.run("Write the model.")

        self.assertEqual(
            service.calls,
            [("execute_agent4design_sync", {"request": {}, "approved": True})],
        )
        self.assertTrue(result.tool_calls[0].human_approved)

    def test_model_visible_write_schema_does_not_include_approval(self):
        agent = OpenAICompatibleAgent(FakeService(), FakeClient([]), model="test-model")
        tools = {
            item["function"]["name"]: item["function"]
            for item in agent.tool_definitions()
        }
        schema = tools["execute_agent4design_sync"]["parameters"]
        self.assertNotIn("approved", schema["properties"])
        self.assertNotIn("approved", schema["required"])


if __name__ == "__main__":
    unittest.main()
