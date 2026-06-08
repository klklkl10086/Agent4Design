"""Generate and import standalone activity XMI artifacts."""

from pathlib import Path
from typing import Union

from pydantic import BaseModel

from agent4design.domain.models import ActivityGraph, FunctionSpec
from agent4design.domain.validators import validate_activity_graph
from agent4design.tools.tool import sanitize_identifier
from agent4design.xmi.generator import generate_activity_xmi
from agent4design.xmi.importer import XMIImportResult, import_xmi_file


class ActivitySyncResult(BaseModel):
    """Result of generating and importing one standalone activity."""

    function_name: str
    activity_name: str
    operation_path: str = ""
    operation_xmi_id: str = ""
    container_path: str = ""
    container_xmi_id: str = ""
    xmi_path: str
    import_result: XMIImportResult


class ActivitySyncService:
    """Run phase-one activity sync without assuming Function ownership mapping."""

    def __init__(
        self,
        toolkit_bat: Union[str, Path],
        output_dir: Union[str, Path] = "xmi_read",
        log_dir: Union[str, Path] = "xmi_import_logs",
        timeout: int = 600,
    ) -> None:
        self.toolkit_bat = toolkit_bat
        self.output_dir = output_dir
        self.log_dir = log_dir
        self.timeout = timeout

    def sync(
        self,
        function_spec: FunctionSpec,
        graph: ActivityGraph,
        *,
        operation_xmi_id: str = "",
        operation_path: str = "",
        package_name: str = "",
        container_xmi_id: str = "",
        container_name: str = "",
        container_meta_class: str = "",
        container_path: str = "",
    ) -> ActivitySyncResult:
        """Validate, generate, and import a UML Activity package."""
        validate_activity_graph(graph)
        xmi_path = generate_activity_xmi(
            function_spec,
            graph,
            self.output_dir,
            package_name=package_name,
        )
        import_result = import_xmi_file(
            xmi_path,
            self.toolkit_bat,
            self.log_dir,
            self.timeout,
        )
        return ActivitySyncResult(
            function_name=function_spec.name,
            activity_name=f"AD_{sanitize_identifier(function_spec.name)}",
            operation_path=operation_path,
            operation_xmi_id=operation_xmi_id,
            container_path=container_path,
            container_xmi_id=container_xmi_id,
            xmi_path=xmi_path,
            import_result=import_result,
        )
