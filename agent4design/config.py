"""Environment-backed configuration for local Agent4Design adapters."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Agent4DesignSettings:
    """Runtime settings shared by CLI, MCP, and workflow adapters."""

    xmi_toolkit_bat: Optional[Path] = None
    xmi_output_dir: Path = Path("xmi_read")
    xmi_log_dir: Path = Path("xmi_import_logs")
    xmi_timeout: int = 600
    create_placeholder_type: bool = False
    enable_activity_import: bool = False
    require_write_approval: bool = True
    mcp_transport: str = "stdio"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    api_token: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_max_tool_rounds: int = 8

    @classmethod
    def from_env(cls) -> "Agent4DesignSettings":
        """Load settings without requiring an additional settings package."""
        toolkit = os.getenv("AGENT4DESIGN_XMI_TOOLKIT_BAT", "").strip()
        return cls(
            xmi_toolkit_bat=Path(toolkit) if toolkit else None,
            xmi_output_dir=Path(os.getenv("AGENT4DESIGN_XMI_OUTPUT_DIR", "xmi_read")),
            xmi_log_dir=Path(os.getenv("AGENT4DESIGN_XMI_LOG_DIR", "xmi_import_logs")),
            xmi_timeout=_env_int("AGENT4DESIGN_XMI_TIMEOUT", 600),
            create_placeholder_type=_env_bool(
                "AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE",
                False,
            ),
            enable_activity_import=_env_bool(
                "AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT",
                False,
            ),
            require_write_approval=_env_bool(
                "AGENT4DESIGN_REQUIRE_WRITE_APPROVAL",
                True,
            ),
            mcp_transport=os.getenv("AGENT4DESIGN_MCP_TRANSPORT", "stdio").strip()
            or "stdio",
            api_host=os.getenv("AGENT4DESIGN_API_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            api_port=_env_int("AGENT4DESIGN_API_PORT", 8765),
            api_token=os.getenv("AGENT4DESIGN_API_TOKEN", "").strip() or None,
            llm_api_key=(
                os.getenv("AGENT4DESIGN_LLM_API_KEY", "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip()
                or None
            ),
            llm_base_url=(
                os.getenv("AGENT4DESIGN_LLM_BASE_URL", "").strip()
                or os.getenv("OPENAI_BASE_URL", "").strip()
                or None
            ),
            llm_model=(
                os.getenv("AGENT4DESIGN_LLM_MODEL", "").strip()
                or os.getenv("OPENAI_MODEL", "").strip()
                or None
            ),
            llm_max_tool_rounds=_env_int("AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS", 8),
        )


def build_agent_service(settings: Optional[Agent4DesignSettings] = None):
    """Construct the framework-neutral service from adapter configuration."""
    from agent4design.rhapsody.context import rhapsody_context
    from agent4design.rhapsody.repository import RhapsodyRepository
    from agent4design.rhapsody.type_registry import TypeRegistry
    from agent4design.services.activity_sync import ActivitySyncService
    from agent4design.services.agent_service import Agent4DesignService

    resolved = settings or Agent4DesignSettings.from_env()
    registry = TypeRegistry(rhapsody_context)
    repository = RhapsodyRepository(
        rhapsody_context,
        registry,
        create_placeholder_type=resolved.create_placeholder_type,
    )
    activity_sync_service = None
    if resolved.enable_activity_import:
        if resolved.xmi_toolkit_bat is None:
            raise RuntimeError(
                "Activity import is enabled but AGENT4DESIGN_XMI_TOOLKIT_BAT is not set."
            )
        activity_sync_service = ActivitySyncService(
            resolved.xmi_toolkit_bat,
            resolved.xmi_output_dir,
            resolved.xmi_log_dir,
            resolved.xmi_timeout,
        )
    return Agent4DesignService(
        context=rhapsody_context,
        type_registry=registry,
        repository=repository,
        activity_sync_service=activity_sync_service,
        require_write_approval=resolved.require_write_approval,
    )
