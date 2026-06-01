"""Expose Agent4Design tools through a small dependency-free HTTP API."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

from agent4design.config import Agent4DesignSettings, build_agent_service


MAX_REQUEST_BYTES = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
ServiceProvider = Callable[[], Any]


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


class Agent4DesignHTTPServer(ThreadingHTTPServer):
    """Cache one service instance for all local API requests."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service_provider: ServiceProvider,
        *,
        api_token: Optional[str] = None,
    ) -> None:
        super().__init__(server_address, Agent4DesignRequestHandler)
        self._service_provider = service_provider
        self._service = None
        self._service_lock = Lock()
        self.api_token = api_token

    @property
    def service_initialized(self) -> bool:
        return self._service is not None

    def get_service(self) -> Any:
        if self._service is not None:
            return self._service
        with self._service_lock:
            if self._service is None:
                self._service = self._service_provider()
        return self._service


class Agent4DesignRequestHandler(BaseHTTPRequestHandler):
    """Handle health checks, tool discovery, and validated tool calls."""

    server: Agent4DesignHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(
            _to_jsonable(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        expected = self.server.api_token
        if expected is None:
            return True
        authorization = self.headers.get("Authorization", "")
        token = self.headers.get("X-Agent4Design-Token", "")
        return authorization == f"Bearer {expected}" or token == expected

    def _require_authorization(self) -> bool:
        if self._is_authorized():
            return True
        self._write_json(
            HTTPStatus.UNAUTHORIZED,
            {"success": False, "error": "Missing or invalid API token."},
        )
        return False

    def _read_json_object(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer.") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"Request body must not exceed {MAX_REQUEST_BYTES} bytes.")
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be a UTF-8 JSON object.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _get_service(self) -> Any | None:
        try:
            return self.server.get_service()
        except Exception as exc:
            self._write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "success": False,
                    "error": f"Agent4Design service could not be initialized: {exc}",
                },
            )
            return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._write_json(
                HTTPStatus.OK,
                {
                    "name": "Agent4Design API",
                    "version": "0.1.0",
                    "endpoints": ["/health", "/tools", "/call", "/tools/{name}"],
                },
            )
            return
        if path == "/health":
            self._write_json(
                HTTPStatus.OK,
                {
                    "success": True,
                    "status": "ok",
                    "service_initialized": self.server.service_initialized,
                },
            )
            return
        if path != "/tools":
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"success": False, "error": f"Unknown endpoint: {path}"},
            )
            return
        if not self._require_authorization():
            return
        service = self._get_service()
        if service is not None:
            self._write_json(HTTPStatus.OK, {"tools": service.list_tools()})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._require_authorization():
            return
        try:
            payload = self._read_json_object()
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"success": False, "error": str(exc)},
            )
            return

        if path == "/call":
            name = payload.get("name")
            arguments = payload.get("arguments", {})
            if not isinstance(name, str) or not name:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": "Field 'name' must be a non-empty string."},
                )
                return
        elif path.startswith("/tools/"):
            name = unquote(path.removeprefix("/tools/"))
            arguments = payload
            if not name:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": "Tool name must not be empty."},
                )
                return
        else:
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"success": False, "error": f"Unknown endpoint: {path}"},
            )
            return

        if not isinstance(arguments, dict):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"success": False, "error": "Tool arguments must be a JSON object."},
            )
            return
        service = self._get_service()
        if service is not None:
            self._write_json(HTTPStatus.OK, service.call(name, arguments))


def create_http_server(
    service: Any | None = None,
    settings: Optional[Agent4DesignSettings] = None,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> Agent4DesignHTTPServer:
    """Create the HTTP server without starting its request loop."""
    resolved = settings or Agent4DesignSettings.from_env()
    resolved_host = host or resolved.api_host
    resolved_port = port if port is not None else resolved.api_port
    if resolved_host not in LOOPBACK_HOSTS and resolved.api_token is None:
        raise RuntimeError(
            "AGENT4DESIGN_API_TOKEN is required when the API listens outside localhost."
        )
    provider = (lambda: service) if service is not None else (lambda: build_agent_service(resolved))
    return Agent4DesignHTTPServer(
        (resolved_host, resolved_port),
        provider,
        api_token=resolved.api_token,
    )


def main() -> None:
    """Run the local Agent4Design HTTP API until interrupted."""
    defaults = Agent4DesignSettings.from_env()
    parser = argparse.ArgumentParser(description="Run the local Agent4Design HTTP API.")
    parser.add_argument("--host", default=defaults.api_host)
    parser.add_argument("--port", default=defaults.api_port, type=int)
    args = parser.parse_args()
    server = create_http_server(settings=defaults, host=args.host, port=args.port)
    print(f"Agent4Design API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
