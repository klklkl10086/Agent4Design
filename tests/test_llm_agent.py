"""Offline tests for the OpenAI-compatible Agent adapter."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import unittest

from agent4design.adapters.llm.openai_compatible import (
    OpenAICodeSegmentExtractor,
    OpenAICompatibleAgent,
)
from agent4design.config import Agent4DesignSettings
from agent4design.services.code_extractor import CodeSyntaxSegment
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
            AgentToolDefinition(
                name="execute_code_path_modeling",
                description="Extract code and write approved changes.",
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
        self.assertEqual(
            client.chat.completions.requests[0]["temperature"],
            0.1,
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
        for name in ("execute_agent4design_sync", "execute_code_path_modeling"):
            schema = tools[name]["parameters"]
            self.assertNotIn("approved", schema["properties"])
            self.assertNotIn("approved", schema["required"])

    def test_code_segment_extractor_sends_parser_segment_to_model(self):
        client = FakeClient(
            [
                _message(
                    '{"segment_id":"segment-1",'
                    '"functions":[{"name":"add","arguments":[],'
                    '"return_type_info":{"base_type":"int"}}]}'
                )
            ]
        )
        extractor = OpenAICodeSegmentExtractor(client, model="test-model")
        result = extractor.extract(
            CodeSyntaxSegment(
                id="segment-1",
                path="demo.c",
                language="c",
                kind="function_definition",
                symbol="add",
                start_line=1,
                end_line=3,
                start_byte=0,
                end_byte=32,
                source="int add(void) { return 1; }",
            ),
            include_activities=False,
        )

        request = client.chat.completions.requests[0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["temperature"], 0.1)
        self.assertEqual(result.functions[0].name, "add")
        self.assertIn("int add", request["messages"][1]["content"])

    def test_legacy_agent_creation_defaults_are_preserved(self):
        settings = Agent4DesignSettings()

        self.assertEqual(settings.llm_model, "VIO:Claude 4.6 Sonnet")
        self.assertEqual(settings.llm_base_url, "https://vio.automotive-wan.com:446")
        self.assertEqual(settings.llm_temperature, 0.1)
        self.assertEqual(settings.llm_max_tool_rounds, 30)
        self.assertEqual(settings.llm_max_retries, 3)

    def test_legacy_header_environment_shape_is_supported(self):
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "AGENT4DESIGN_LLM_API_KEY=",
                        "OPENAI_API_KEY=",
                        "AGENT4DESIGN_LLM_BASE_URL=",
                        "OPENAI_BASE_URL=",
                        "API_TOKEN=token",
                        "BASE_URL=https://example.test",
                        'VIO_HEADERS={"X-Tenant-ID":"legacy"}',
                        'AGENT4DESIGN_LLM_HEADERS={"X-Tenant-ID":"agent4design"}',
                    ]
                ),
                encoding="utf-8",
            )
            with patch("agent4design.config.Path.cwd", return_value=Path(directory)):
                settings = Agent4DesignSettings.from_env()

        self.assertEqual(settings.llm_api_key, "token")
        self.assertEqual(settings.llm_base_url, "https://example.test")
        self.assertEqual(settings.llm_header, {"X-Tenant-ID": "agent4design"})

    def test_system_environment_is_ignored_for_settings(self):
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "AGENT4DESIGN_LLM_API_KEY=",
                        "OPENAI_API_KEY=",
                        "API_TOKEN=",
                        "AGENT4DESIGN_LLM_BASE_URL=",
                        "OPENAI_BASE_URL=",
                        "BASE_URL=",
                    ]
                ),
                encoding="utf-8",
            )
            with patch("agent4design.config.Path.cwd", return_value=Path(directory)):
                with patch.dict(
                    "os.environ",
                    {
                        "API_TOKEN": "ignored",
                        "BASE_URL": "https://ignored.test",
                    },
                    clear=True,
                ):
                    settings = Agent4DesignSettings.from_env()

        self.assertIsNone(settings.llm_api_key)
        self.assertEqual(settings.llm_base_url, "https://vio.automotive-wan.com:446")


if __name__ == "__main__":
    unittest.main()
