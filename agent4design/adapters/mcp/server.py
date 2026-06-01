"""Expose Agent4Design as a local MCP server."""

from __future__ import annotations

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


def _require_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP support is optional. Install the 'mcp' extra with "
            "`pip install -e .[mcp]`."
        ) from exc
    return FastMCP


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def create_mcp_server(
    service: Optional[Agent4DesignService] = None,
    settings: Optional[Agent4DesignSettings] = None,
):
    """Create a FastMCP server without starting its transport."""
    FastMCP = _require_fastmcp()
    resolved = settings or Agent4DesignSettings.from_env()
    agent_service = service or build_agent_service(resolved)
    mcp = FastMCP("Agent4Design")

    @mcp.tool()
    def initialize_rhapsody(select_current_target: bool = True) -> Dict[str, Any]:
        """Connect to Rhapsody and optionally capture the selected model target."""
        return _dump(
            agent_service.call(
                "initialize_rhapsody",
                {"select_current_target": select_current_target},
            )
        )

    @mcp.tool()
    def select_rhapsody_target() -> Dict[str, Any]:
        """Refresh the writable target from the current Rhapsody GUI selection."""
        return _dump(agent_service.call("select_rhapsody_target"))

    @mcp.tool()
    def get_rhapsody_context() -> Dict[str, Any]:
        """Return the active project and selected target summary."""
        return _dump(agent_service.call("get_rhapsody_context"))

    @mcp.tool()
    def refresh_type_registry() -> Dict[str, Any]:
        """Scan active-project Type and Class metadata without exposing COM objects."""
        return _dump(agent_service.call("refresh_type_registry"))

    @mcp.tool()
    def plan_agent4design_sync(request: Dict[str, Any]) -> Dict[str, Any]:
        """Build a read-only plan for functions, variables, macros, and activities."""
        return _dump(agent_service.call("plan_agent4design_sync", request))

    @mcp.tool()
    def execute_agent4design_sync(
        request: Dict[str, Any],
        approved: bool = False,
        verify_after_sync: bool = True,
    ) -> Dict[str, Any]:
        """Execute approved Rhapsody changes and return a verification report."""
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
    def verify_rhapsody_model(request: Dict[str, Any]) -> Dict[str, Any]:
        """Run read-only COM checks for expected model elements."""
        return _dump(agent_service.call("verify_rhapsody_model", request))

    @mcp.tool()
    def generate_standalone_activity_xmi(
        function_spec: Dict[str, Any],
        graph: Dict[str, Any],
        output_dir: str = "xmi_read",
    ) -> Dict[str, Any]:
        """Generate standalone activity XMI without importing it into Rhapsody."""
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
    ) -> Dict[str, Any]:
        """Import an experimental standalone Activity package after approval."""
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
    def save_rhapsody_project(approved: bool = False) -> Dict[str, Any]:
        """Save the active Rhapsody project after explicit approval."""
        return _dump(
            agent_service.call(
                "save_rhapsody_project",
                {"approved": approved},
            )
        )

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
            [reference.model_dump(mode="json") for reference in agent_service.type_registry.references],
            ensure_ascii=False,
            indent=2,
        )

    return mcp


def main() -> None:
    """Start the local MCP server using the configured transport."""
    settings = Agent4DesignSettings.from_env()
    server = create_mcp_server(settings=settings)
    server.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
