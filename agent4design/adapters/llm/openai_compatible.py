"""OpenAI-compatible tool-calling Agent for Agent4Design."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import httpx
from pydantic import Field

from agent4design.config import Agent4DesignSettings, build_agent_service
from agent4design.domain.models import StrictModel
from agent4design.services.agent_service import (
    Agent4DesignService,
    AgentToolDefinition,
    AgentToolResult,
)


WRITE_TOOLS = {
    "execute_agent4design_sync",
    "execute_code_path_modeling",
    "save_rhapsody_project",
}

DEFAULT_SYSTEM_PROMPT = """\
You are Agent4Design, an IBM Rhapsody modeling assistant.
Use tools to inspect the active project and build a read-only synchronization
plan before requesting changes. Never claim that a write succeeded unless the
tool result reports success. Writes require approval from the human operator;
you cannot grant approval yourself. Activity XMI import is experimental until
Function ownership mapping is verified manually. Keep responses concise and
surface rejected types or verification failures clearly.
"""

DEFAULT_VIO_HEADERS = {
    "useLegacyCompletionsEndpoint": "false",
    "X-Tenant-ID": "default_tenant",
}

ApprovalHandler = Callable[[str, Dict[str, Any]], bool]


class ToolExecutionRecord(StrictModel):
    """One model-requested tool invocation and its validated local result."""

    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    human_approved: bool = False
    result: AgentToolResult


class AgentRunResult(StrictModel):
    """Serializable result from one user turn and any tool-call rounds."""

    response: str
    tool_calls: List[ToolExecutionRecord] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]

    return value


def _tool_definition(definition: AgentToolDefinition) -> Dict[str, Any]:
    schema = deepcopy(definition.input_schema)

    if definition.name in WRITE_TOOLS:
        properties = schema.get("properties", {})
        properties.pop("approved", None)

        required = schema.get("required", [])
        schema["required"] = [
            name for name in required if name != "approved"
        ]

    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": schema,
        },
    }


def _assistant_message(message: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "role": "assistant",
        "content": message.content or "",
    }

    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in message.tool_calls
        ]

    return payload


def _resolve_httpx_verify(
    settings: Agent4DesignSettings,
) -> Union[bool, str]:
    """
    Resolve httpx certificate verification behavior.

    Cases:
    1. AGENT4DESIGN_LLM_CA_BUNDLE is set:
       verify uses that CA bundle path.

    2. AGENT4DESIGN_LLM_SSL_VERIFY=false:
       verify=False.

    3. Default:
       verify=True.
    """
    if settings.llm_ca_bundle is not None:
        ca_path = Path(settings.llm_ca_bundle)
        if not ca_path.exists():
            raise RuntimeError(
                "AGENT4DESIGN_LLM_CA_BUNDLE is set, but the file does not "
                f"exist: {ca_path}"
            )
        return str(ca_path)

    return settings.llm_ssl_verify


def _build_http_client(settings: Agent4DesignSettings) -> httpx.Client:
    verify_setting = _resolve_httpx_verify(settings)

    return httpx.Client(
        verify=verify_setting,
        timeout=httpx.Timeout(
            connect=60.0,
            read=300.0,
            write=60.0,
            pool=60.0,
        ),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
        ),
    )


class OpenAICompatibleAgent:
    """Drive Agent4DesignService through an OpenAI-compatible chat API."""

    def __init__(
        self,
        service: Agent4DesignService,
        client: Any,
        *,
        model: str,
        max_tool_rounds: int = 8,
        approval_handler: Optional[ApprovalHandler] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if not model.strip():
            raise ValueError("A non-empty model name is required.")

        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be at least 1.")

        self.service = service
        self.client = client
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.approval_handler = approval_handler
        self.system_prompt = system_prompt

    def tool_definitions(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function tools without model-set approvals."""
        return [_tool_definition(tool) for tool in self.service.list_tools()]

    def _execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> ToolExecutionRecord:
        call_arguments = dict(arguments)
        human_approved = False

        if name in WRITE_TOOLS:
            call_arguments["approved"] = False

            if self.approval_handler is None or not self.approval_handler(
                name,
                arguments,
            ):
                return ToolExecutionRecord(
                    name=name,
                    arguments=arguments,
                    result=AgentToolResult(
                        name=name,
                        success=False,
                        error=(
                            "Human approval was not granted for this write "
                            "operation."
                        ),
                    ),
                )

            call_arguments["approved"] = True
            human_approved = True

        return ToolExecutionRecord(
            name=name,
            arguments=arguments,
            human_approved=human_approved,
            result=self.service.call(name, call_arguments),
        )

    def run(
        self,
        user_message: str,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentRunResult:
        """Run one user turn until the model responds without tool calls."""
        conversation = list(
            messages
            or [{"role": "system", "content": self.system_prompt}]
        )
        conversation.append({"role": "user", "content": user_message})

        records: List[ToolExecutionRecord] = []
        tools = self.tool_definitions()

        for _ in range(self.max_tool_rounds):
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=list(conversation),
                tools=tools,
                tool_choice="auto",
            )

            message = completion.choices[0].message
            conversation.append(_assistant_message(message))

            if not message.tool_calls:
                return AgentRunResult(
                    response=message.content or "",
                    tool_calls=records,
                    messages=conversation,
                )

            for tool_call in message.tool_calls:
                try:
                    arguments = json.loads(
                        tool_call.function.arguments or "{}"
                    )

                    if not isinstance(arguments, dict):
                        raise ValueError(
                            "Tool arguments must be a JSON object."
                        )

                    record = self._execute_tool(
                        tool_call.function.name,
                        arguments,
                    )

                except Exception as exc:
                    record = ToolExecutionRecord(
                        name=tool_call.function.name,
                        result=AgentToolResult(
                            name=tool_call.function.name,
                            success=False,
                            error=str(exc),
                        ),
                    )

                records.append(record)

                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            _jsonable(record.result),
                            ensure_ascii=False,
                        ),
                    }
                )

        raise RuntimeError(
            "Model exceeded the configured limit of "
            f"{self.max_tool_rounds} tool rounds."
        )


def create_openai_compatible_agent(
    settings: Optional[Agent4DesignSettings] = None,
    *,
    service: Optional[Agent4DesignService] = None,
    client: Any = None,
    approval_handler: Optional[ApprovalHandler] = None,
) -> OpenAICompatibleAgent:
    """Build an API-backed Agent from environment settings."""
    resolved = settings or Agent4DesignSettings.from_env()

    if resolved.llm_model is None:
        raise RuntimeError(
            "Set AGENT4DESIGN_LLM_MODEL or OPENAI_MODEL before starting "
            "the Agent."
        )

    if client is None:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "LLM support is optional. Install the 'llm' extra with "
                "`pip install -e .[llm]`."
            ) from exc

        if not resolved.llm_api_key:
            raise RuntimeError(
                "Set AGENT4DESIGN_LLM_API_KEY, OPENAI_API_KEY, or API_TOKEN "
                "before starting the Agent."
            )

        if not resolved.llm_base_url:
            raise RuntimeError(
                "Set AGENT4DESIGN_LLM_BASE_URL or OPENAI_BASE_URL before "
                "starting the Agent."
            )

        custom_http_client = _build_http_client(resolved)

        client = openai.OpenAI(
            api_key=resolved.llm_api_key,
            base_url=resolved.llm_base_url,
            default_headers=resolved.llm_header or DEFAULT_VIO_HEADERS,
            http_client=custom_http_client,
            max_retries=3,
        )

    return OpenAICompatibleAgent(
        service or build_agent_service(resolved),
        client,
        model=resolved.llm_model,
        max_tool_rounds=resolved.llm_max_tool_rounds,
        approval_handler=approval_handler,
    )
