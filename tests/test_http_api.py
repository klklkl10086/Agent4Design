"""Offline contract tests for the dependency-free HTTP adapter."""

from __future__ import annotations

import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent4design.adapters.http.server import create_http_server
from agent4design.config import Agent4DesignSettings


class FakeService:
    """Small framework-neutral service used without Rhapsody or pywin32."""

    @staticmethod
    def list_tools():
        return [{"name": "echo", "description": "Return arguments.", "input_schema": {}}]

    @staticmethod
    def call(name, arguments):
        if name != "echo":
            return {"name": name, "success": False, "error": "unknown tool"}
        return {"name": name, "success": True, "output": arguments}


class HTTPAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_http_server(
            FakeService(),
            Agent4DesignSettings(api_host="127.0.0.1", api_port=0),
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _request(self, path: str, payload=None, headers=None):
        body = None
        method = "GET"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            method = "POST"
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_does_not_initialize_service(self) -> None:
        status, payload = self._request("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["service_initialized"])

    def test_lists_tools_and_calls_generic_route(self) -> None:
        status, tools = self._request("/tools")
        self.assertEqual(status, 200)
        self.assertEqual(tools["tools"][0]["name"], "echo")

        status, result = self._request(
            "/call",
            {"name": "echo", "arguments": {"message": "hello"}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])
        self.assertEqual(result["output"], {"message": "hello"})

    def test_calls_named_tool_route(self) -> None:
        status, result = self._request("/tools/echo", {"message": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(result["output"], {"message": "hello"})

    def test_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(HTTPError) as context:
            self._request("/call", {"name": "echo", "arguments": []})
        self.assertEqual(context.exception.code, 400)


class HTTPAdapterSecurityTests(unittest.TestCase):
    def test_requires_token_outside_loopback(self) -> None:
        with self.assertRaises(RuntimeError):
            create_http_server(
                FakeService(),
                Agent4DesignSettings(api_host="0.0.0.0", api_port=0),
            )

    def test_token_protects_tool_endpoints(self) -> None:
        server = create_http_server(
            FakeService(),
            Agent4DesignSettings(api_host="127.0.0.1", api_port=0, api_token="secret"),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        try:
            with self.assertRaises(HTTPError) as context:
                urlopen(f"{base_url}/tools", timeout=2)
            self.assertEqual(context.exception.code, 401)

            request = Request(
                f"{base_url}/tools",
                headers={"Authorization": "Bearer secret"},
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["tools"][0]["name"], "echo")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
