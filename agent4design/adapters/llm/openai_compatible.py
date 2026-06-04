"""OpenAI-compatible tool-calling Agent for Agent4Design."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

import httpx
from pydantic import Field

from agent4design.config import Agent4DesignSettings, build_agent_service
from agent4design.domain.models import StrictModel
from agent4design.services.code_extractor import (
    CodeBatchExtraction,
    CodeSegmentExtraction,
    CodeSyntaxSegment,
    parse_batch_extraction_json,
    parse_segment_extraction_json,
)
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
For C/H code, use the direct CODE-to-tool flow:
1. If the user pastes CODE, read it from the message and generate the JSON
   arguments for plan_agent4design_sync directly.
2. If the user provides a local C/H file path, call read_code_path first, then
   generate the JSON arguments for plan_agent4design_sync directly from the
   returned CODE.
3. read_code_path may return one syntax chunk at a time. After each CODE chunk,
   call plan_agent4design_sync for the model elements visible in that chunk.
   If has_more is true, call read_code_path again with next_chunk_index and
   continue. Do not wait to read every chunk before calling tools.
4. Do not call extract_code_path_model or plan_code_path_modeling unless the
   user explicitly asks to use the legacy extractor.
5. Extract only model elements needed by the sync tool: macros, variables, and
   function signatures. Do not derive activity graphs from function bodies
   unless the user explicitly asks for activity diagrams.
Use execute_agent4design_sync only after human approval is available.
"""

DEFAULT_VIO_HEADERS = {
    "useLegacyCompletionsEndpoint": "false",
    "X-Tenant-ID": "default_tenant",
}

CODE_EXTRACTION_SYSTEM_PROMPT = """\
You extract IBM Rhapsody modeling specs from parser-identified C/C++ source
segments. Segment boundaries were produced by tree-sitter; do not invent code
outside the provided source and context. Return JSON only. Match the provided
schema. Use empty arrays when a segment does not define a model element.
"""

ApprovalHandler = Callable[[str, Dict[str, Any]], bool]
StreamTextHandler = Callable[[str], None]
StreamStatusHandler = Callable[[str], None]


def _create_chat_completion_with_retries(
    completions: Any,
    *,
    max_retries: int,
    **kwargs: Any,
) -> Any:
    """Call chat completions with legacy-style bounded retry behavior."""
    attempts = max(1, max_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return completions.create(**kwargs)
        except TypeError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(60.0, 2.0 * (2 ** (attempt - 1))))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Chat completion failed without an exception.")


def _create_streaming_chat_completion_with_retries(
    completions: Any,
    *,
    max_retries: int,
    **kwargs: Any,
) -> Iterable[Any]:
    """Create a streaming chat completion with bounded startup retries."""
    attempts = max(1, max_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return completions.create(stream=True, **kwargs)
        except TypeError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(60.0, 2.0 * (2 ** (attempt - 1))))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Streaming chat completion failed without an exception.")


def _value(payload: Any, name: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _first_choice(payload: Any) -> Any:
    choices = _value(payload, "choices", [])
    return choices[0] if choices else None


def _streamed_assistant_message(
    chunks: Iterable[Any],
    *,
    on_delta: Optional[StreamTextHandler] = None,
) -> Dict[str, Any]:
    """Accumulate OpenAI-compatible streamed deltas into one assistant message."""
    content_parts: List[str] = []
    tool_calls: Dict[int, Dict[str, Any]] = {}

    for chunk in chunks:
        choice = _first_choice(chunk)
        if choice is None:
            continue

        delta = _value(choice, "delta", None)
        if delta is None:
            continue

        content = _value(delta, "content", None)
        if content:
            content_parts.append(content)
            if on_delta is not None:
                on_delta(content)

        for tool_delta in _value(delta, "tool_calls", []) or []:
            index = _value(tool_delta, "index", len(tool_calls))
            current = tool_calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )

            call_id = _value(tool_delta, "id", None)
            if call_id:
                current["id"] = call_id

            call_type = _value(tool_delta, "type", None)
            if call_type:
                current["type"] = call_type

            function_delta = _value(tool_delta, "function", None)
            if function_delta is None:
                continue

            function = current["function"]
            name = _value(function_delta, "name", None)
            if name:
                function["name"] += name

            arguments = _value(function_delta, "arguments", None)
            if arguments:
                function["arguments"] += arguments

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts),
    }
    if tool_calls:
        message["tool_calls"] = [
            call for _, call in sorted(tool_calls.items(), key=lambda item: item[0])
        ]
    return message


def _message_has_tool_calls(message: Dict[str, Any]) -> bool:
    return bool(message.get("tool_calls"))


def _tool_error_summary(result: AgentToolResult) -> str:
    if result.error:
        return result.error

    output = result.output
    errors = []
    if hasattr(output, "errors"):
        errors = getattr(output, "errors", []) or []
    elif isinstance(output, dict):
        errors = output.get("errors", []) or []

    if errors:
        return str(errors[0])

    return ""


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


class OpenAICodeSegmentExtractor:
    """Ask an OpenAI-compatible model to extract specs from one syntax segment."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        temperature: float = 0.1,
        max_retries: int = 3,
        system_prompt: str = CODE_EXTRACTION_SYSTEM_PROMPT,
        status_handler: Optional[StreamStatusHandler] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.system_prompt = system_prompt
        self.status_handler = status_handler

    def set_status_handler(
        self,
        status_handler: Optional[StreamStatusHandler],
    ) -> None:
        self.status_handler = status_handler

    def _complete_json(self, messages: List[Dict[str, Any]]) -> str:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            completion = _create_chat_completion_with_retries(
                self.client.chat.completions,
                max_retries=self.max_retries,
                **kwargs,
            )
        except TypeError:
            kwargs.pop("response_format", None)
            completion = _create_chat_completion_with_retries(
                self.client.chat.completions,
                max_retries=self.max_retries,
                **kwargs,
            )
        return completion.choices[0].message.content or "{}"

    def extract(
        self,
        segment: CodeSyntaxSegment,
        *,
        include_activities: bool,
    ) -> CodeSegmentExtraction:
        label = segment.symbol or segment.id
        if self.status_handler is not None:
            self.status_handler(
                "Extracting "
                f"{segment.kind} {label} "
                f"from {Path(segment.path).name}:{segment.start_line}..."
            )

        schema = CodeSegmentExtraction.model_json_schema()
        payload = segment.model_dump(mode="json")
        payload["include_activities"] = include_activities
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": schema,
                        "segment": payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        content = self._complete_json(messages)
        result = parse_segment_extraction_json(content, segment_id=segment.id)

        if self.status_handler is not None:
            self.status_handler(
                "Finished "
                f"{segment.kind} {label} "
                f"from {Path(segment.path).name}:{segment.start_line}."
            )

        return result

    def extract_many(
        self,
        segments: List[CodeSyntaxSegment],
        *,
        include_activities: bool,
    ) -> List[CodeSegmentExtraction]:
        if not segments:
            return []

        first = segments[0]
        last = segments[-1]
        if self.status_handler is not None:
            self.status_handler(
                "Extracting "
                f"{len(segments)} syntax segments from "
                f"{Path(first.path).name}:{first.start_line}-"
                f"{Path(last.path).name}:{last.end_line}..."
            )

        schema = CodeBatchExtraction.model_json_schema()
        payload = [segment.model_dump(mode="json") for segment in segments]
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": schema,
                        "include_activities": include_activities,
                        "segments": payload,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        content = self._complete_json(messages)
        result = parse_batch_extraction_json(content)
        segment_results = list(result.segment_results)
        if result.warnings or result.errors:
            if segment_results:
                first_result = segment_results[0]
                segment_results[0] = first_result.model_copy(
                    update={
                        "warnings": [*first_result.warnings, *result.warnings],
                        "errors": [*first_result.errors, *result.errors],
                    }
                )
            else:
                segment_results.append(
                    CodeSegmentExtraction(
                        segment_id=first.id,
                        warnings=result.warnings,
                        errors=result.errors,
                    )
                )

        if self.status_handler is not None:
            self.status_handler(
                "Finished "
                f"{len(segments)} syntax segments from "
                f"{Path(first.path).name}:{first.start_line}-"
                f"{Path(last.path).name}:{last.end_line}."
            )

        return segment_results


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
        max_tool_rounds: int = 30,
        temperature: float = 0.1,
        max_retries: int = 3,
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
        self.temperature = temperature
        self.max_retries = max_retries
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

    def _set_code_extractor_status_handler(
        self,
        status_handler: Optional[StreamStatusHandler],
    ) -> None:
        extractor = getattr(self.service, "code_model_extractor", None)
        setter = getattr(extractor, "set_status_handler", None)
        if callable(setter):
            setter(status_handler)

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
            completion = _create_chat_completion_with_retries(
                self.client.chat.completions,
                max_retries=self.max_retries,
                model=self.model,
                messages=list(conversation),
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
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

    def run_stream(
        self,
        user_message: str,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
        on_delta: Optional[StreamTextHandler] = None,
        on_status: Optional[StreamStatusHandler] = None,
    ) -> AgentRunResult:
        """
        Run one user turn with streamed assistant text and tool progress events.

        Tool-call rounds are streamed and accumulated before local execution.
        When the model requests tools, status callbacks are emitted while the
        local tool runs; the final assistant response is emitted through
        ``on_delta`` as text deltas.
        """
        conversation = list(
            messages
            or [{"role": "system", "content": self.system_prompt}]
        )
        conversation.append({"role": "user", "content": user_message})

        records: List[ToolExecutionRecord] = []
        tools = self.tool_definitions()
        self._set_code_extractor_status_handler(on_status)

        try:
            for _ in range(self.max_tool_rounds):
                kwargs = {
                    "model": self.model,
                    "messages": list(conversation),
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": self.temperature,
                }

                try:
                    chunks = _create_streaming_chat_completion_with_retries(
                        self.client.chat.completions,
                        max_retries=self.max_retries,
                        **kwargs,
                    )
                    assistant_payload = _streamed_assistant_message(
                        chunks,
                        on_delta=on_delta,
                    )
                except TypeError:
                    completion = _create_chat_completion_with_retries(
                        self.client.chat.completions,
                        max_retries=self.max_retries,
                        **kwargs,
                    )
                    assistant_payload = _assistant_message(
                        completion.choices[0].message
                    )
                    if not _message_has_tool_calls(assistant_payload):
                        content = assistant_payload.get("content", "")
                        if content and on_delta is not None:
                            on_delta(content)
                except Exception as exc:
                    if on_status is not None:
                        on_status(
                            "Streaming response failed; retrying without "
                            f"streaming: {exc}"
                        )
                    completion = _create_chat_completion_with_retries(
                        self.client.chat.completions,
                        max_retries=self.max_retries,
                        **kwargs,
                    )
                    assistant_payload = _assistant_message(
                        completion.choices[0].message
                    )
                    if not _message_has_tool_calls(assistant_payload):
                        content = assistant_payload.get("content", "")
                        if content and on_delta is not None:
                            on_delta(content)

                conversation.append(assistant_payload)

                if not _message_has_tool_calls(assistant_payload):
                    return AgentRunResult(
                        response=assistant_payload.get("content", ""),
                        tool_calls=records,
                        messages=conversation,
                    )

                for tool_call in assistant_payload.get("tool_calls", []):
                    function = tool_call.get("function", {})
                    name = function.get("name", "")
                    if on_status is not None:
                        on_status(f"Calling tool: {name}")

                    try:
                        arguments = json.loads(
                            function.get("arguments") or "{}"
                        )

                        if not isinstance(arguments, dict):
                            raise ValueError(
                                "Tool arguments must be a JSON object."
                            )

                        record = self._execute_tool(name, arguments)

                    except Exception as exc:
                        record = ToolExecutionRecord(
                            name=name,
                            result=AgentToolResult(
                                name=name,
                                success=False,
                                error=str(exc),
                            ),
                        )

                    records.append(record)

                    if on_status is not None:
                        outcome = "succeeded" if record.result.success else "failed"
                        summary = _tool_error_summary(record.result)
                        detail = f": {summary}" if summary else ""
                        on_status(f"Tool {name} {outcome}{detail}.")

                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", ""),
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
        finally:
            self._set_code_extractor_status_handler(None)
    

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
            "Edit .env and set AGENT4DESIGN_LLM_MODEL or OPENAI_MODEL before "
            "starting the Agent."
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
                "Edit .env and set AGENT4DESIGN_LLM_API_KEY, OPENAI_API_KEY, "
                "or API_TOKEN before starting the Agent."
            )

        if not resolved.llm_base_url:
            raise RuntimeError(
                "Edit .env and set AGENT4DESIGN_LLM_BASE_URL or OPENAI_BASE_URL "
                "before starting the Agent."
            )

        custom_http_client = _build_http_client(resolved)

        client = openai.OpenAI(
            api_key=resolved.llm_api_key,
            base_url=resolved.llm_base_url,
            default_headers={**(resolved.llm_header or DEFAULT_VIO_HEADERS)},
            http_client=custom_http_client,
            max_retries=resolved.llm_max_retries,
        )

    service_instance = service
    if service_instance is None:
        service_instance = build_agent_service(
            resolved,
            code_model_extractor=OpenAICodeSegmentExtractor(
                client,
                model=resolved.llm_model,
                temperature=resolved.llm_temperature,
                max_retries=resolved.llm_max_retries,
            ),
        )

    return OpenAICompatibleAgent(
        service_instance,
        client,
        model=resolved.llm_model,
        max_tool_rounds=resolved.llm_max_tool_rounds,
        temperature=resolved.llm_temperature,
        max_retries=resolved.llm_max_retries,
        approval_handler=approval_handler,
    )
