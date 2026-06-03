""".env-backed configuration for local Agent4Design adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Mapping, Optional

from dotenv import dotenv_values


DEFAULT_VIO_BASE_URL = "https://vio.automotive-wan.com:446"
DEFAULT_VIO_MODEL = "VIO:Claude 4.6 Sonnet"


def _env_value(values: Mapping[str, str], name: str, default: str = "") -> str:
    value = values.get(name)
    if value is None:
        return default
    return str(value)


def _env_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    value = values.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    value = values.get(name)
    return int(value) if value else default


def _env_float(values: Mapping[str, str], name: str, default: float) -> float:
    value = values.get(name)
    return float(value) if value else default


def _env_json(values: Mapping[str, str], name: str) -> Optional[dict]:
    value = _env_value(values, name).strip()
    if not value:
        return None

    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ValueError(
            f".env setting {name} must be valid JSON. "
            f"Current value: {value}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f".env setting {name} must be a JSON object. "
            f"Current value: {value}"
        )

    return parsed


def _env_json_first(values: Mapping[str, str], *names: str) -> Optional[dict]:
    for name in names:
        parsed = _env_json(values, name)
        if parsed is not None:
            return parsed
    return None


def _load_env_values() -> Dict[str, str]:
    """
    Read .env files in a stable order without consulting process environment.

    Priority:
    1. Current working directory .env
       Example: C:\\Users\\uik00187\\Desktop\\Agent4Design\\.env

    2. Project root .env
       Example: C:\\Users\\uik00187\\Desktop\\Agent4Design\\.env

    3. Package directory .env
       Example: C:\\Users\\uik00187\\Desktop\\Agent4Design\\agent4design\\.env

    Earlier files win. OS-level environment variables are intentionally ignored
    so runtime configuration is controlled only by editing .env files.
    """
    current_file = Path(__file__).resolve()
    package_dir = current_file.parent
    project_root = package_dir.parent

    candidates = [
        Path.cwd() / ".env",
        project_root / ".env",
        package_dir / ".env",
    ]

    loaded = set()
    values: Dict[str, str] = {}
    for env_path in candidates:
        env_path = env_path.resolve()
        if env_path in loaded:
            continue
        loaded.add(env_path)

        if env_path.exists():
            for name, value in dotenv_values(env_path).items():
                if value is not None:
                    values.setdefault(name, value)

    return values


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
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8766
    mcp_path: str = "/mcp"
    mcp_token: Optional[str] = None
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    api_token: Optional[str] = None

    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = DEFAULT_VIO_BASE_URL
    llm_model: Optional[str] = DEFAULT_VIO_MODEL
    llm_max_tool_rounds: int = 30
    llm_temperature: float = 0.1
    llm_max_retries: int = 3
    llm_header: Optional[dict] = None

    # SSL / certificate settings for OpenAI-compatible LLM endpoint.
    # For your current VIO certificate issue, set:
    # AGENT4DESIGN_LLM_SSL_VERIFY=false
    #
    # For the formal solution, set:
    # AGENT4DESIGN_LLM_SSL_VERIFY=true
    # AGENT4DESIGN_LLM_CA_BUNDLE=C:\\path\\to\\vio_root_ca.pem
    llm_ssl_verify: bool = True
    llm_ca_bundle: Optional[Path] = None

    @classmethod
    def from_env(cls) -> "Agent4DesignSettings":
        """Load settings only from .env files."""

        values = _load_env_values()

        toolkit = _env_value(values, "AGENT4DESIGN_XMI_TOOLKIT_BAT").strip()
        ca_bundle = _env_value(values, "AGENT4DESIGN_LLM_CA_BUNDLE").strip()

        return cls(
            xmi_toolkit_bat=Path(toolkit) if toolkit else None,
            xmi_output_dir=Path(
                _env_value(values, "AGENT4DESIGN_XMI_OUTPUT_DIR", "xmi_read")
            ),
            xmi_log_dir=Path(
                _env_value(values, "AGENT4DESIGN_XMI_LOG_DIR", "xmi_import_logs")
            ),
            xmi_timeout=_env_int(values, "AGENT4DESIGN_XMI_TIMEOUT", 600),

            create_placeholder_type=_env_bool(
                values,
                "AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE",
                False,
            ),
            enable_activity_import=_env_bool(
                values,
                "AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT",
                bool(toolkit),
            ),
            require_write_approval=_env_bool(
                values,
                "AGENT4DESIGN_REQUIRE_WRITE_APPROVAL",
                True,
            ),

            mcp_transport=(
                _env_value(values, "AGENT4DESIGN_MCP_TRANSPORT", "stdio").strip()
                or "stdio"
            ),
            mcp_host=(
                _env_value(
                    values,
                    "AGENT4DESIGN_MCP_HOST",
                    _env_value(values, "AGENT4DESIGN_API_HOST", "127.0.0.1"),
                ).strip()
                or "127.0.0.1"
            ),
            mcp_port=_env_int(values, "AGENT4DESIGN_MCP_PORT", 8766),
            mcp_path=(
                _env_value(values, "AGENT4DESIGN_MCP_PATH", "/mcp").strip()
                or "/mcp"
            ),
            mcp_token=(
                _env_value(values, "AGENT4DESIGN_MCP_TOKEN").strip()
                or _env_value(values, "AGENT4DESIGN_API_TOKEN").strip()
                or None
            ),
            api_host=(
                _env_value(values, "AGENT4DESIGN_API_HOST", "127.0.0.1").strip()
                or "127.0.0.1"
            ),
            api_port=_env_int(values, "AGENT4DESIGN_API_PORT", 8765),
            api_token=(
                _env_value(values, "AGENT4DESIGN_API_TOKEN").strip()
                or None
            ),

            # LLM key priority:
            # 1. AGENT4DESIGN_LLM_API_KEY: your project's preferred variable
            # 2. OPENAI_API_KEY: common OpenAI-compatible variable
            # 3. API_TOKEN: official VIO template variable
            llm_api_key=(
                _env_value(values, "AGENT4DESIGN_LLM_API_KEY").strip()
                or _env_value(values, "OPENAI_API_KEY").strip()
                or _env_value(values, "API_TOKEN").strip()
                or None
            ),
            llm_base_url=(
                _env_value(values, "AGENT4DESIGN_LLM_BASE_URL").strip()
                or _env_value(values, "OPENAI_BASE_URL").strip()
                or _env_value(values, "BASE_URL").strip()
                or DEFAULT_VIO_BASE_URL
            ),
            llm_model=(
                _env_value(values, "AGENT4DESIGN_LLM_MODEL").strip()
                or _env_value(values, "OPENAI_MODEL").strip()
                or DEFAULT_VIO_MODEL
            ),
            llm_max_tool_rounds=_env_int(
                values,
                "AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS",
                30,
            ),
            llm_temperature=_env_float(
                values,
                "AGENT4DESIGN_LLM_TEMPERATURE",
                0.1,
            ),
            llm_max_retries=_env_int(
                values,
                "AGENT4DESIGN_LLM_MAX_RETRIES",
                3,
            ),
            llm_header=_env_json_first(
                values,
                "AGENT4DESIGN_LLM_HEADERS",
                "VIO_HEADERS",
            ),

            llm_ssl_verify=_env_bool(
                values,
                "AGENT4DESIGN_LLM_SSL_VERIFY",
                True,
            ),
            llm_ca_bundle=Path(ca_bundle) if ca_bundle else None,
        )


def build_agent_service(
    settings: Optional[Agent4DesignSettings] = None,
    *,
    code_model_extractor=None,
):
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
                "Activity import is enabled but AGENT4DESIGN_XMI_TOOLKIT_BAT "
                "is not set. Set it to the XMI Toolkit batch file path, then "
                "restart the service."
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
        code_model_extractor=code_model_extractor,
        require_write_approval=resolved.require_write_approval,
    )
