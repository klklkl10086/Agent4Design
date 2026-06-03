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
    CodePathExtractionRequest,
    CodePathExtractionResult,
    ExtractedActivitySpec,
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

    def sync_model(self, request: ModelSyncRequest) -> ModelSyncResult:
        """Synchronize macros, variables, and functions through COM."""
        return self.model_sync_service.sync(request)

    def sync_activity(self, request: ActivitySyncRequest) -> ActivitySyncResult:
        """Generate and import one standalone activity XMI artifact."""
        if self.activity_sync_service is None:
            raise RuntimeError(
                "Activity sync is not configured. This is a service startup "
                "configuration issue and retrying the same request will not fix it. "
                "Set AGENT4DESIGN_XMI_TOOLKIT_BAT to the XMI Toolkit batch file "
                "path and restart the service. You can also set "
                "AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true explicitly, or construct "
                "the service with Agent4DesignService.with_xmi_toolkit(...)."
            )
        return self.activity_sync_service.sync(
            request.function_spec,
            request.graph,
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
        return extract_code_path_model(request)

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
        for activity in request.activities:
            activity_name = f"activity_{sanitize_identifier(activity.function_spec.name)}"
            try:
                from agent4design.domain.validators import validate_activity_graph

                validate_activity_graph(activity.graph)
                activities.append(
                    ActivityPlanItem(
                        function_name=activity.function_spec.name,
                        activity_name=activity_name,
                        success=True,
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
                f"activity_{sanitize_identifier(item.function_name)}"
                for item in sync_result.activities
                if item.success
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
            ("extract_code_path_model", "Extract model specs from a C source file or directory.", CodePathExtractionRequest),
            ("plan_code_path_modeling", "Extract code and build a read-only modeling plan.", CodePathModelingRequest),
            ("execute_code_path_modeling", "Extract code, execute approved Rhapsody modeling, and verify it.", ExecuteCodePathModelingRequest),
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
