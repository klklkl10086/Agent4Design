"""Offline tests for the optional MCP adapter wiring."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from agent4design.adapters.mcp import server as mcp_server
from agent4design.config import Agent4DesignSettings
from agent4design.services.agent_service import AgentToolResult


class FakeFastMCP:
    """Small decorator-compatible stand-in for the MCP SDK."""

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.settings = SimpleNamespace()
        self.tools = {}
        self.resources = {}
        self.run_calls = []

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, uri: str):
        def decorator(func):
            self.resources[uri] = func
            return func

        return decorator

    def run(self, transport: str) -> None:
        self.run_calls.append(transport)


class FakeService:
    def __init__(self):
        self.calls = []
        self.type_registry = SimpleNamespace(references=[])

    def call(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        return AgentToolResult(
            name=name,
            success=True,
            output=arguments or {},
        )


class MCPAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_require_fastmcp = mcp_server._require_fastmcp
        mcp_server._require_fastmcp = lambda: FakeFastMCP

    def tearDown(self) -> None:
        mcp_server._require_fastmcp = self.original_require_fastmcp

    def test_registers_service_tools_and_checks_token(self) -> None:
        service = FakeService()
        server = mcp_server.create_mcp_server(
            service=service,
            settings=Agent4DesignSettings(mcp_token="secret"),
        )

        self.assertIn("extract_code_path_model", server.tools)
        self.assertIn("save_type_index", server.tools)
        self.assertIn("load_type_index", server.tools)
        self.assertEqual(server.resources, {})

        with self.assertRaises(PermissionError):
            server.tools["get_rhapsody_context"](auth_token="wrong")

        payload = server.tools["save_type_index"](
            path="types.json",
            auth_token="secret",
        )
        self.assertTrue(payload["success"])
        self.assertEqual(
            service.calls[-1],
            ("save_type_index", {"path": "types.json"}),
        )

    def test_registers_read_only_resources_without_token(self) -> None:
        server = mcp_server.create_mcp_server(
            service=FakeService(),
            settings=Agent4DesignSettings(),
        )

        self.assertIn("rhapsody://context", server.resources)
        self.assertIn("rhapsody://types", server.resources)

    def test_http_transport_falls_back_to_streamable_http_name(self) -> None:
        class FallbackFastMCP(FakeFastMCP):
            def run(self, transport: str) -> None:
                self.run_calls.append(transport)
                if transport == "http":
                    raise ValueError("unsupported transport")

        server = FallbackFastMCP("Agent4Design")

        mcp_server._run_fastmcp(server, "http")

        self.assertEqual(server.run_calls, ["http", "streamable-http"])


if __name__ == "__main__":
    unittest.main()
