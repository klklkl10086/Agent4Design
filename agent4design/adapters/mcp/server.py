"""Expose Agent4Design as a local MCP server."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Dict, Optional

from agent4design.config import Agent4DesignSettings, build_agent_service
from agent4design.domain.models import ActivityGraph, FunctionSpec
from agent4design.services.agent_service import (
    ActivitySyncRequest,
    Agent4DesignService,
)
from agent4design.xmi.generator import generate_activity_xmi


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
HTTP_TRANSPORTS = {"http", "streamable-http"}


def _require_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP support is optional. Install the 'mcp' extra with "
            "`pip install -e .[mcp]`."
        ) from exc
    return FastMCP


def _is_loopback_host(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _make_fastmcp(FastMCP: Any, settings: Agent4DesignSettings):
    """Create FastMCP while tolerating small SDK signature differences."""
    kwargs = {
        "host": settings.mcp_host,
        "port": settings.mcp_port,
        "streamable_http_path": settings.mcp_path,
        "json_response": True,
    }
    try:
        return FastMCP("Agent4Design", **kwargs)
    except TypeError:
        mcp = FastMCP("Agent4Design")
        fastmcp_settings = getattr(mcp, "settings", None)
        if fastmcp_settings is not None:
            for name, value in (
                ("host", settings.mcp_host),
                ("port", settings.mcp_port),
                ("streamable_http_path", settings.mcp_path),
                ("path", settings.mcp_path),
            ):
                try:
                    setattr(fastmcp_settings, name, value)
                except Exception:
                    pass
        return mcp


def _run_fastmcp(server: Any, transport: str) -> None:
    """Run FastMCP and fall back across official/standalone transport names."""
    candidates = [transport]
    if transport == "http":
        candidates.append("streamable-http")
    elif transport == "streamable-http":
        candidates.append("http")

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            server.run(transport=candidate)
            return
        except (TypeError, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def create_mcp_server(
    service: Optional[Agent4DesignService] = None,
    settings: Optional[Agent4DesignSettings] = None,
):
    """Create a FastMCP server without starting its transport."""
    FastMCP = _require_fastmcp()
    resolved = settings or Agent4DesignSettings.from_env()
    agent_service = service or build_agent_service(resolved)
    mcp = _make_fastmcp(FastMCP, resolved)

    def require_token(auth_token: str = "") -> None:
        if resolved.mcp_token is None:
            return
        if auth_token != resolved.mcp_token:
            raise PermissionError("Missing or invalid Agent4Design MCP token.")

    @mcp.tool()
    def initialize_rhapsody(
        select_current_target: bool = True,
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Connect to Rhapsody and optionally capture the selected model target."""
        require_token(auth_token)
        return _dump(
            agent_service.call(
                "initialize_rhapsody",
                {"select_current_target": select_current_target},
            )
        )

    @mcp.tool()
    def select_rhapsody_target(auth_token: str = "") -> Dict[str, Any]:
        """Refresh the writable target from the current Rhapsody GUI selection."""
        require_token(auth_token)
        return _dump(agent_service.call("select_rhapsody_target"))

    @mcp.tool()
    def get_rhapsody_context(auth_token: str = "") -> Dict[str, Any]:
        """Return the active project and selected target summary."""
        require_token(auth_token)
        return _dump(agent_service.call("get_rhapsody_context"))

    @mcp.tool()
    def refresh_type_registry(auth_token: str = "") -> Dict[str, Any]:
        """Scan active-project Type and Class metadata without exposing COM objects."""
        require_token(auth_token)
        return _dump(agent_service.call("refresh_type_registry"))

    @mcp.tool()
    def save_type_index(path: str, auth_token: str = "") -> Dict[str, Any]:
        """Save the serializable type metadata index for diagnostics."""
        require_token(auth_token)
        return _dump(agent_service.call("save_type_index", {"path": path}))

    @mcp.tool()
    def load_type_index(path: str, auth_token: str = "") -> Dict[str, Any]:
        """Load saved type metadata; COM objects are relocated on later use."""
        require_token(auth_token)
        return _dump(agent_service.call("load_type_index", {"path": path}))

    @mcp.tool()
    def extract_code_path_model(
        request: Dict[str, Any],
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Extract model specs from a C source file or directory."""
        require_token(auth_token)
        return _dump(agent_service.call("extract_code_path_model", request))

    @mcp.tool()
    def plan_code_path_modeling(
        request: Dict[str, Any],
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Extract code and build a read-only modeling plan."""
        require_token(auth_token)
        return _dump(agent_service.call("plan_code_path_modeling", request))

    @mcp.tool()
    def execute_code_path_modeling(
        request: Dict[str, Any],
        approved: bool = False,
        verify_after_sync: bool = True,
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Extract code and execute approved end-to-end Rhapsody modeling."""
        require_token(auth_token)
        return _dump(
            agent_service.call(
                "execute_code_path_modeling",
                {
                    "request": request,
                    "approved": approved,
                    "verify_after_sync": verify_after_sync,
                },
            )
        )

    @mcp.tool()
    def plan_agent4design_sync(
        request: Dict[str, Any],
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Build a read-only plan for functions, variables, macros, and activities."""
        require_token(auth_token)
        return _dump(agent_service.call("plan_agent4design_sync", request))

    @mcp.tool()
    def execute_agent4design_sync(
        request: Dict[str, Any],
        approved: bool = False,
        verify_after_sync: bool = True,
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Execute approved Rhapsody changes and return a verification report."""
        require_token(auth_token)
        return _dump(
            agent_service.call(
                "execute_agent4design_sync",
                {
                    "request": request,
                    "approved": approved,
                    "verify_after_sync": verify_after_sync,
                },
            )
        )

    @mcp.tool()
    def verify_rhapsody_model(
        request: Dict[str, Any],
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Run read-only COM checks for expected model elements."""
        require_token(auth_token)
        return _dump(agent_service.call("verify_rhapsody_model", request))

    @mcp.tool()
    def generate_standalone_activity_xmi(
        function_spec: Dict[str, Any],
        graph: Dict[str, Any],
        output_dir: str = "xmi_read",
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Generate standalone activity XMI without importing it into Rhapsody."""
        require_token(auth_token)
        path = generate_activity_xmi(
            FunctionSpec.model_validate(function_spec),
            ActivityGraph.model_validate(graph),
            output_dir,
        )
        return {"xmi_path": path}

    @mcp.tool()
    def import_standalone_activity_xmi(
        function_spec: Dict[str, Any],
        graph: Dict[str, Any],
        approved: bool = False,
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Import an experimental standalone Activity package after approval."""
        require_token(auth_token)
        if resolved.require_write_approval and not approved:
            raise PermissionError("Activity import requires explicit approval.")
        result = agent_service.sync_activity(
            ActivitySyncRequest(
                function_spec=FunctionSpec.model_validate(function_spec),
                graph=ActivityGraph.model_validate(graph),
            )
        )
        return _dump(result)

    @mcp.tool()
    def save_rhapsody_project(
        approved: bool = False,
        auth_token: str = "",
    ) -> Dict[str, Any]:
        """Save the active Rhapsody project after explicit approval."""
        require_token(auth_token)
        return _dump(
            agent_service.call(
                "save_rhapsody_project",
                {"approved": approved},
            )
        )

    if resolved.mcp_token is None:
        @mcp.resource("rhapsody://context")
        def rhapsody_context_resource() -> str:
            """Return cached context as a read-only MCP resource."""
            return json.dumps(
                _dump(agent_service.call("get_rhapsody_context")),
                ensure_ascii=False,
                indent=2,
            )

        @mcp.resource("rhapsody://types")
        def rhapsody_types_resource() -> str:
            """Return serializable type metadata as a read-only MCP resource."""
            return json.dumps(
                [
                    reference.model_dump(mode="json")
                    for reference in agent_service.type_registry.references
                ],
                ensure_ascii=False,
                indent=2,
            )

    return mcp


def _settings_from_args(defaults: Agent4DesignSettings) -> Agent4DesignSettings:
    parser = argparse.ArgumentParser(
        description=(
            "Run Agent4Design MCP on the machine that has IBM Rhapsody open."
        )
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default=defaults.mcp_transport,
        help="stdio for local MCP clients, http/streamable-http for remote clients.",
    )
    parser.add_argument("--host", default=defaults.mcp_host)
    parser.add_argument("--port", type=int, default=defaults.mcp_port)
    parser.add_argument("--path", default=defaults.mcp_path)
    parser.add_argument(
        "--token",
        default=defaults.mcp_token,
        help="Required for non-localhost HTTP MCP. Passed by tools as auth_token.",
    )
    args = parser.parse_args()
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    return replace(
        defaults,
        mcp_transport=args.transport,
        mcp_host=args.host,
        mcp_port=args.port,
        mcp_path=path,
        mcp_token=args.token or None,
    )


def main() -> None:
    """Start the local MCP server using the configured transport."""
    settings = _settings_from_args(Agent4DesignSettings.from_env())
    if settings.mcp_transport in HTTP_TRANSPORTS:
        if not _is_loopback_host(settings.mcp_host) and settings.mcp_token is None:
            raise RuntimeError(
                "AGENT4DESIGN_MCP_TOKEN or --token is required when MCP HTTP "
                "listens outside localhost."
            )
        print(
            "Agent4Design MCP listening on "
            f"http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}"
        )
    server = create_mcp_server(settings=settings)
    _run_fastmcp(server, settings.mcp_transport)


if __name__ == "__main__":
    main()
