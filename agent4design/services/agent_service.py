"""Framework-neutral tool service for Agent, LangGraph, or MCP adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import Field

from agent4design.domain.models import ActivityGraph, FunctionSpec, StrictModel
from agent4design.rhapsody.context import RhapsodyContext, rhapsody_context
from agent4design.rhapsody.repository import RhapsodyRepository
from agent4design.rhapsody.type_registry import TypeRegistry
from agent4design.rhapsody.verifier import RhapsodyVerifier
from agent4design.services.activity_sync import ActivitySyncResult, ActivitySyncService
from agent4design.services.code_extractor import (
    CodeSegmentModelExtractor,
    CodePathExtractionRequest,
    CodePathExtractionResult,
    ExtractedActivitySpec,
    TreeSitterCodeSegmenter,
    extract_code_path_model,
)
from agent4design.services.model_sync import (
    ModelSyncRequest,
    ModelSyncResult,
    ModelSyncService,
)
from agent4design.services.sync_plan import ModelSyncPlan, SyncPlanService
from agent4design.services.verification import (
    VerificationRequest,
    VerificationService,
)
from agent4design.rhapsody.verifier import VerificationReport
from agent4design.tools.tool import sanitize_identifier


class EmptyRequest(StrictModel):
    """Request model for tools that do not require arguments."""


class InitializeRequest(StrictModel):
    """Initialize COM and optionally capture the selected model container."""

    select_current_target: bool = True


class ApprovalRequest(StrictModel):
    """Explicit approval for a standalone write operation."""

    approved: bool = False


class TypeIndexPathRequest(StrictModel):
    """Filesystem location for a serializable type metadata index."""

    path: str = Field(..., min_length=1)


class ReadCodePathRequest(StrictModel):
    """Read a C/H source file so the Agent model can reason over CODE directly."""

    path: str = Field(..., min_length=1)
    max_bytes: int = Field(120_000, ge=1)
    encoding: str = "auto"
    chunk_index: int = Field(0, ge=0)
    max_chunk_chars: int = Field(30_000, ge=1)
    syntax_chunks: bool = True


class ReadCodePathResult(StrictModel):
    """Source code content returned to the Agent model."""

    path: str
    bytes_read: int
    encoding: str
    truncated: bool = False
    chunk_index: int = 0
    chunk_count: int = 1
    has_more: bool = False
    next_chunk_index: Optional[int] = None
    chunk_start_line: int = 1
    chunk_end_line: int = 1
    chunk_segment_count: int = 0
    content: str
    error: str = ""


class ActivitySyncRequest(StrictModel):
    """Function signature and standalone activity graph to import."""

    function_spec: FunctionSpec
    graph: ActivityGraph


class AgentSyncRequest(StrictModel):
    """Complete local Agent synchronization request."""

    model: ModelSyncRequest = Field(default_factory=ModelSyncRequest)
    activities: List[ActivitySyncRequest] = Field(default_factory=list)


class AgentActivitySyncResult(StrictModel):
    """Observable result for one activity synchronization attempt."""

    function_name: str
    success: bool
    result: Optional[ActivitySyncResult] = None
    error: str = ""


class AgentSyncResult(StrictModel):
    """Combined semantic model and standalone activity synchronization report."""

    success: bool
    model: ModelSyncResult
    activities: List[AgentActivitySyncResult] = Field(default_factory=list)


class ActivityPlanItem(StrictModel):
    """Dry-run validation result for one standalone activity import."""

    function_name: str
    activity_name: str
    mounted_to_operation: bool = False
    operation_path: str = ""
    success: bool
    experimental: bool = True
    error: str = ""


class AgentSyncPlanResult(StrictModel):
    """Approval-ready dry-run report for semantic and activity synchronization."""

    success: bool
    requires_approval: bool
    model: ModelSyncPlan
    activities: List[ActivityPlanItem] = Field(default_factory=list)


class ExecuteSyncRequest(StrictModel):
    """Approved request for changing the active Rhapsody model."""

    request: AgentSyncRequest
    approved: bool = False
    verify_after_sync: bool = True


class ExecuteSyncResult(StrictModel):
    """Observable result of an approved Agent4Design synchronization."""

    success: bool
    plan: AgentSyncPlanResult
    sync: AgentSyncResult
    verification: Optional[VerificationReport] = None
    saved: bool = False
    save_error: str = ""


class CodePathModelingRequest(CodePathExtractionRequest):
    """Code path plus synchronization options for end-to-end modeling."""

    continue_on_error: bool = True
    save_project: bool = False


class CodePathPlanResult(StrictModel):
    """Read-only extraction and synchronization plan for a code path."""

    success: bool
    extraction: CodePathExtractionResult
    plan: Optional[AgentSyncPlanResult] = None


class ExecuteCodePathModelingRequest(StrictModel):
    """Approved end-to-end modeling request from a C source path."""

    request: CodePathModelingRequest
    approved: bool = False
    verify_after_sync: bool = True


class ExecuteCodePathModelingResult(StrictModel):
    """Result of extracting code specs and executing approved modeling."""

    success: bool
    extraction: CodePathExtractionResult
    execution: Optional[ExecuteSyncResult] = None
    error: str = ""


class AgentToolDefinition(StrictModel):
    """JSON-schema description consumed by an Agent adapter."""

    name: str
    description: str
    input_schema: Dict[str, Any]


class AgentToolResult(StrictModel):
    """Serializable result returned by the generic Agent call endpoint."""

    name: str
    success: bool
    output: Any = None
    error: str = ""


class Agent4DesignService:
    """Stable Agent-facing facade over COM and XMI application services."""

    def __init__(
        self,
        context: RhapsodyContext = rhapsody_context,
        type_registry: Optional[TypeRegistry] = None,
        repository: Optional[RhapsodyRepository] = None,
        model_sync_service: Optional[ModelSyncService] = None,
        activity_sync_service: Optional[ActivitySyncService] = None,
        code_model_extractor: Optional[CodeSegmentModelExtractor] = None,
        sync_plan_service: Optional[SyncPlanService] = None,
        verification_service: Optional[VerificationService] = None,
        require_write_approval: bool = True,
    ) -> None:
        self.context = context
        if model_sync_service is not None and repository is None:
            repository = model_sync_service.repository
        if repository is not None and type_registry is None:
            type_registry = getattr(repository, "type_registry", None)

        self.type_registry = type_registry or TypeRegistry(context)
        self.repository = repository or RhapsodyRepository(context, self.type_registry)
        self.model_sync_service = model_sync_service or ModelSyncService(
            self.repository,
            context,
        )
        self.activity_sync_service = activity_sync_service
        self.code_model_extractor = code_model_extractor
        self.sync_plan_service = sync_plan_service or SyncPlanService(self.repository)
        self.verification_service = verification_service or VerificationService(
            RhapsodyVerifier(context)
        )
        self.require_write_approval = require_write_approval

    @classmethod
    def with_xmi_toolkit(
        cls,
        toolkit_bat: Union[str, Path],
        *,
        output_dir: Union[str, Path] = "xmi_read",
        log_dir: Union[str, Path] = "xmi_import_logs",
        timeout: int = 600,
    ) -> "Agent4DesignService":
        """Construct a facade that can also import standalone activities."""
        return cls(
            activity_sync_service=ActivitySyncService(
                toolkit_bat,
                output_dir,
                log_dir,
                timeout,
            )
        )

    def initialize(self, request: InitializeRequest) -> Dict[str, str]:
        """Connect to Rhapsody and optionally capture the GUI selection."""
        if request.select_current_target:
            self.context.initialize()
        else:
            self.context.connect()
        return self.context.get_summary()

    def select_current_target(self, request: EmptyRequest) -> Dict[str, str]:
        """Refresh the writable target from the current Rhapsody selection."""
        self.context.select_current_target()
        return self.context.get_summary()

    def get_context(self, request: EmptyRequest) -> Dict[str, str]:
        """Return strings that are safe to expose outside the COM thread."""
        return self.context.get_summary()

    def refresh_type_registry(self, request: EmptyRequest) -> Dict[str, int]:
        """Rebuild the active-project type metadata index."""
        self.type_registry.refresh()
        return {"type_count": len(self.type_registry.references)}

    def save_type_index(self, request: TypeIndexPathRequest) -> Dict[str, Any]:
        """Persist serializable type metadata for diagnostics."""
        self.type_registry.save_index(request.path)
        return {
            "path": str(Path(request.path).resolve()),
            "type_count": len(self.type_registry.references),
        }

    def load_type_index(self, request: TypeIndexPathRequest) -> Dict[str, Any]:
        """Load type metadata; COM objects are still relocated on use."""
        self.type_registry.load_index(request.path)
        return {
            "path": str(Path(request.path).resolve()),
            "type_count": len(self.type_registry.references),
        }

    @staticmethod
    def _decode_code_bytes(
        data: bytes,
        encoding: str,
    ) -> tuple[str, str, str]:
        encodings = (
            [encoding]
            if encoding and encoding.lower() != "auto"
            else ["utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"]
        )
        last_error = ""
        for candidate in encodings:
            try:
                return data.decode(candidate), candidate, ""
            except UnicodeDecodeError as exc:
                last_error = str(exc)

        return data.decode("utf-8", errors="replace"), "utf-8-replace", last_error

    @staticmethod
    def _line_chunks(
        content: str,
        max_chunk_chars: int,
    ) -> List[tuple[str, int, int, int]]:
        lines = content.splitlines()
        if not lines:
            return [("", 1, 1, 0)]

        chunks: List[tuple[str, int, int, int]] = []
        current: List[str] = []
        start_line = 1
        current_chars = 0

        for index, line in enumerate(lines, start=1):
            line_chars = len(line) + 1
            if current and current_chars + line_chars > max_chunk_chars:
                chunks.append(("\n".join(current), start_line, index - 1, 0))
                current = []
                start_line = index
                current_chars = 0
            current.append(line)
            current_chars += line_chars

        if current:
            chunks.append(("\n".join(current), start_line, len(lines), 0))

        return chunks

    @staticmethod
    def _syntax_chunks(
        path: Path,
        content: str,
        request: ReadCodePathRequest,
    ) -> List[tuple[str, int, int, int]]:
        try:
            segments = TreeSitterCodeSegmenter().segment_file(
                path,
                content,
                CodePathExtractionRequest(
                    path=str(path),
                    include_segments=True,
                    require_model_extraction=False,
                    max_context_segments=0,
                ),
            )
        except Exception:
            return Agent4DesignService._line_chunks(
                content,
                request.max_chunk_chars,
            )

        if not segments:
            return Agent4DesignService._line_chunks(
                content,
                request.max_chunk_chars,
            )

        lines = content.splitlines()
        line_count = max(1, len(lines))
        chunks: List[tuple[str, int, int, int]] = []
        start_line = 1
        end_line = 1
        segment_count = 0

        for segment in sorted(segments, key=lambda item: item.start_byte):
            candidate_end = min(max(segment.end_line, 1), line_count)
            candidate = "\n".join(lines[start_line - 1 : candidate_end])
            if (
                segment_count > 0
                and len(candidate) > request.max_chunk_chars
            ):
                text = "\n".join(lines[start_line - 1 : end_line])
                chunks.append((text, start_line, end_line, segment_count))
                start_line = end_line + 1
                candidate_end = min(max(segment.end_line, start_line), line_count)
                segment_count = 0

            end_line = candidate_end
            segment_count += 1

        if end_line < line_count:
            end_line = line_count

        text = "\n".join(lines[start_line - 1 : end_line])
        chunks.append((text, start_line, end_line, segment_count))
        return chunks

    @staticmethod
    def read_code_path(request: ReadCodePathRequest) -> ReadCodePathResult:
        """Read source code text for direct LLM-to-tool JSON generation."""
        path = Path(request.path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Code file not found: {path}")

        data = path.read_bytes()
        truncated = len(data) > request.max_bytes
        if truncated:
            data = data[: request.max_bytes]

        content, encoding, error = Agent4DesignService._decode_code_bytes(
            data,
            request.encoding,
        )
        chunks = (
            Agent4DesignService._syntax_chunks(path, content, request)
            if request.syntax_chunks
            else Agent4DesignService._line_chunks(content, request.max_chunk_chars)
        )

        if request.chunk_index >= len(chunks):
            raise IndexError(
                f"chunk_index {request.chunk_index} is out of range; "
                f"available chunks: {len(chunks)}"
            )

        chunk_content, start_line, end_line, segment_count = chunks[request.chunk_index]
        next_chunk_index = (
            request.chunk_index + 1
            if request.chunk_index + 1 < len(chunks)
            else None
        )

        return ReadCodePathResult(
            path=str(path),
            bytes_read=len(data),
            encoding=encoding,
            truncated=truncated,
            chunk_index=request.chunk_index,
            chunk_count=len(chunks),
            has_more=next_chunk_index is not None,
            next_chunk_index=next_chunk_index,
            chunk_start_line=start_line,
            chunk_end_line=end_line,
            chunk_segment_count=segment_count,
            content=chunk_content,
            error=error,
        )

    def sync_model(self, request: ModelSyncRequest) -> ModelSyncResult:
        """Synchronize macros, variables, and functions through COM."""
        return self.model_sync_service.sync(request)

    def sync_activity(self, request: ActivitySyncRequest) -> ActivitySyncResult:
        """Generate and import one function-mounted activity XMI artifact."""
        if self.activity_sync_service is None:
            raise RuntimeError(
                "Activity sync is not configured. This is a service startup "
                "configuration issue and retrying the same request will not fix it. "
                "Set AGENT4DESIGN_XMI_TOOLKIT_BAT to the XMI Toolkit batch file "
                "path and restart the service. You can also set "
                "AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true explicitly, or construct "
                "the service with Agent4DesignService.with_xmi_toolkit(...)."
            )
        operation_reference = self.repository.resolve_operation_reference(
            request.function_spec.name
        )
        return self.activity_sync_service.sync(
            request.function_spec,
            request.graph,
            operation_xmi_id=operation_reference.xmi_id,
            operation_path=operation_reference.path,
            package_name=self.context.target_name,
            container_xmi_id=operation_reference.container_xmi_id,
            container_name=operation_reference.container_name,
            container_meta_class=operation_reference.container_meta_class,
            container_path=operation_reference.container_path,
        )

    def sync(self, request: AgentSyncRequest) -> AgentSyncResult:
        """Synchronize semantic model elements and requested activities."""
        model_result = self.sync_model(request.model)
        activity_results = []
        for activity in request.activities:
            try:
                result = self.sync_activity(activity)
                activity_results.append(
                    AgentActivitySyncResult(
                        function_name=activity.function_spec.name,
                        success=result.import_result.success,
                        result=result,
                        error=result.import_result.stderr,
                    )
                )
            except Exception as exc:
                activity_results.append(
                    AgentActivitySyncResult(
                        function_name=activity.function_spec.name,
                        success=False,
                        error=str(exc),
                    )
                )

        return AgentSyncResult(
            success=model_result.success
            and all(item.success for item in activity_results),
            model=model_result,
            activities=activity_results,
        )

    @staticmethod
    def _activity_request(activity: ExtractedActivitySpec) -> ActivitySyncRequest:
        return ActivitySyncRequest(
            function_spec=activity.function_spec,
            graph=activity.graph,
        )

    def _sync_request_from_code_path(
        self,
        extraction: CodePathExtractionResult,
        request: CodePathModelingRequest,
    ) -> AgentSyncRequest:
        return AgentSyncRequest(
            model=ModelSyncRequest(
                macros=extraction.macros,
                variables=extraction.variables,
                functions=extraction.functions,
                continue_on_error=request.continue_on_error,
                save_project=request.save_project,
            ),
            activities=[
                self._activity_request(activity)
                for activity in extraction.activities
            ],
        )

    def extract_code_path_model(
        self,
        request: CodePathExtractionRequest,
    ) -> CodePathExtractionResult:
        """Extract model specs from a C source file or directory."""
        return extract_code_path_model(
            request,
            model_extractor=self.code_model_extractor,
        )

    def plan_code_path_modeling(
        self,
        request: CodePathModelingRequest,
    ) -> CodePathPlanResult:
        """Extract code and build a read-only Rhapsody synchronization plan."""
        extraction = self.extract_code_path_model(request)
        if not extraction.success:
            return CodePathPlanResult(success=False, extraction=extraction)

        plan = self.plan_sync(
            self._sync_request_from_code_path(extraction, request)
        )
        return CodePathPlanResult(
            success=plan.success,
            extraction=extraction,
            plan=plan,
        )

    def execute_code_path_modeling(
        self,
        request: ExecuteCodePathModelingRequest,
    ) -> ExecuteCodePathModelingResult:
        """Extract code, execute approved modeling, and return verification."""
        extraction = self.extract_code_path_model(request.request)
        if not extraction.success:
            return ExecuteCodePathModelingResult(
                success=False,
                extraction=extraction,
                error="Code extraction failed.",
            )

        sync_request = self._sync_request_from_code_path(
            extraction,
            request.request,
        )
        try:
            execution = self.execute_sync(
                ExecuteSyncRequest(
                    request=sync_request,
                    approved=request.approved,
                    verify_after_sync=request.verify_after_sync,
                )
            )
            return ExecuteCodePathModelingResult(
                success=execution.success,
                extraction=extraction,
                execution=execution,
            )
        except Exception as exc:
            return ExecuteCodePathModelingResult(
                success=False,
                extraction=extraction,
                error=str(exc),
            )

    def plan_sync(self, request: AgentSyncRequest) -> AgentSyncPlanResult:
        """Build a read-only approval plan for semantic and activity writes."""
        model_plan = self.sync_plan_service.plan(request.model)
        activities = []
        planned_function_names = {
            sanitize_identifier(function.name)
            for function in request.model.functions
        }
        for activity in request.activities:
            function_name = sanitize_identifier(activity.function_spec.name)
            activity_name = f"activity_{function_name}"
            try:
                from agent4design.domain.validators import validate_activity_graph

                validate_activity_graph(activity.graph)
                operation_path = ""
                mounted_to_operation = False
                mount_error = ""
                if function_name in planned_function_names:
                    mounted_to_operation = True
                else:
                    try:
                        operation_reference = self.repository.resolve_operation_reference(
                            activity.function_spec.name
                        )
                        operation_path = operation_reference.path
                        mounted_to_operation = True
                    except Exception as exc:
                        mounted_to_operation = False
                        mount_error = str(exc)
                activities.append(
                    ActivityPlanItem(
                        function_name=activity.function_spec.name,
                        activity_name=activity_name,
                        mounted_to_operation=mounted_to_operation,
                        operation_path=operation_path,
                        success=mounted_to_operation,
                        error=mount_error,
                    )
                )
            except Exception as exc:
                activities.append(
                    ActivityPlanItem(
                        function_name=activity.function_spec.name,
                        activity_name=activity_name,
                        success=False,
                        error=str(exc),
                    )
                )
        return AgentSyncPlanResult(
            success=model_plan.success and all(item.success for item in activities),
            requires_approval=model_plan.requires_approval or bool(activities),
            model=model_plan,
            activities=activities,
        )

    def verify(self, request: VerificationRequest) -> VerificationReport:
        """Run read-only post-sync verification through COM."""
        return self.verification_service.verify(request)

    def execute_sync(self, request: ExecuteSyncRequest) -> ExecuteSyncResult:
        """Execute an approved sync, verify it, then optionally save the project."""
        plan = self.plan_sync(request.request)
        if not plan.success:
            raise RuntimeError("Synchronization plan contains rejected items.")
        if self.require_write_approval and plan.requires_approval and not request.approved:
            raise PermissionError("Synchronization requires explicit approval.")

        should_save = request.request.model.save_project
        model_request = request.request.model.model_copy(update={"save_project": False})
        sync_request = request.request.model_copy(update={"model": model_request})
        sync_result = self.sync(sync_request)

        verification = None
        if request.verify_after_sync and sync_result.success:
            imported_activities = [
                item.result.activity_name
                for item in sync_result.activities
                if item.success and item.result is not None
            ]
            verification = self.verify(
                VerificationRequest(
                    macros=sync_request.model.macros,
                    variables=sync_request.model.variables,
                    functions=sync_request.model.functions,
                    activities=imported_activities,
                )
            )

        saved = False
        save_error = ""
        success = sync_result.success and (
            verification is None or verification.success
        )
        if should_save and success:
            try:
                self.context.save_project()
                saved = True
            except Exception as exc:
                success = False
                save_error = str(exc)

        return ExecuteSyncResult(
            success=success,
            plan=plan,
            sync=sync_result,
            verification=verification,
            saved=saved,
            save_error=save_error,
        )

    def save_project(self, request: EmptyRequest) -> Dict[str, bool]:
        """Save the active Rhapsody project explicitly."""
        self.context.save_project()
        return {"saved": True}

    def save_project_approved(self, request: ApprovalRequest) -> Dict[str, bool]:
        """Save only after the adapter or user grants explicit approval."""
        if self.require_write_approval and not request.approved:
            raise PermissionError("Saving the Rhapsody project requires explicit approval.")
        self.context.save_project()
        return {"saved": True}

    @staticmethod
    def list_tools() -> List[AgentToolDefinition]:
        """List framework-neutral tools and their JSON input schemas."""
        tools = [
            ("initialize_rhapsody", "Connect to Rhapsody and select the current target.", InitializeRequest),
            ("select_rhapsody_target", "Refresh the target from the Rhapsody GUI selection.", EmptyRequest),
            ("get_rhapsody_context", "Get the cached active project and target summary.", EmptyRequest),
            ("refresh_type_registry", "Scan Type and Class metadata from the active project.", EmptyRequest),
            ("save_type_index", "Save the serializable type metadata index.", TypeIndexPathRequest),
            ("load_type_index", "Load type metadata for later COM relocation.", TypeIndexPathRequest),
            ("read_code_path", "Read a C/H file as CODE text, optionally split by syntax chunks. Use this first when the user provides a local code path. For each returned CODE chunk, generate plan_agent4design_sync JSON directly, then call read_code_path again with next_chunk_index when has_more=true.", ReadCodePathRequest),
            ("extract_code_path_model", "Legacy parser/LLM extractor for diagnostics only. Do not use for normal CODE-to-tool JSON modeling unless explicitly requested.", CodePathExtractionRequest),
            ("plan_code_path_modeling", "Legacy automatic code-path extractor plus modeling plan. Prefer read_code_path followed by plan_agent4design_sync.", CodePathModelingRequest),
            ("execute_code_path_modeling", "Legacy automatic code-path extractor plus approved execution. Prefer read_code_path followed by execute_agent4design_sync after approval.", ExecuteCodePathModelingRequest),
            ("plan_agent4design_sync", "Build a read-only synchronization plan for approval.", AgentSyncRequest),
            ("execute_agent4design_sync", "Execute an explicitly approved synchronization and verify the result.", ExecuteSyncRequest),
            ("verify_rhapsody_model", "Run read-only verification against the active Rhapsody project.", VerificationRequest),
            ("save_rhapsody_project", "Save the active Rhapsody project after explicit approval.", ApprovalRequest),
        ]
        return [
            AgentToolDefinition(
                name=name,
                description=description,
                input_schema=model.model_json_schema(),
            )
            for name, description, model in tools
        ]

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> AgentToolResult:
        """Validate and invoke one Agent tool by name."""
        tool_map = {
            "initialize_rhapsody": (InitializeRequest, self.initialize),
            "select_rhapsody_target": (EmptyRequest, self.select_current_target),
            "get_rhapsody_context": (EmptyRequest, self.get_context),
            "refresh_type_registry": (EmptyRequest, self.refresh_type_registry),
            "save_type_index": (TypeIndexPathRequest, self.save_type_index),
            "load_type_index": (TypeIndexPathRequest, self.load_type_index),
            "read_code_path": (ReadCodePathRequest, self.read_code_path),
            "extract_code_path_model": (CodePathExtractionRequest, self.extract_code_path_model),
            "plan_code_path_modeling": (CodePathModelingRequest, self.plan_code_path_modeling),
            "execute_code_path_modeling": (ExecuteCodePathModelingRequest, self.execute_code_path_modeling),
            "plan_agent4design_sync": (AgentSyncRequest, self.plan_sync),
            "execute_agent4design_sync": (ExecuteSyncRequest, self.execute_sync),
            "verify_rhapsody_model": (VerificationRequest, self.verify),
            "save_rhapsody_project": (ApprovalRequest, self.save_project_approved),
        }
        tool = tool_map.get(name)
        if tool is None:
            return AgentToolResult(
                name=name,
                success=False,
                error=f"Unknown Agent4Design tool: {name}",
            )

        request_model, handler = tool
        try:
            request = request_model.model_validate(arguments or {})
            output = handler(request)
            success = getattr(output, "success", True)
            if isinstance(output, ActivitySyncResult):
                success = output.import_result.success
            return AgentToolResult(
                name=name,
                success=success,
                output=output,
            )
        except Exception as exc:
            return AgentToolResult(
                name=name,
                success=False,
                error=str(exc),
            )


agent4design_service = Agent4DesignService()
