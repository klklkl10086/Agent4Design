"""Generate and import standalone activity XMI artifacts."""

from pathlib import Path
from typing import Union

from pydantic import BaseModel

from agent4design.domain.models import ActivityGraph, FunctionSpec
from agent4design.domain.validators import validate_activity_graph
from agent4design.xmi.generator import generate_activity_xmi
from agent4design.xmi.importer import XMIImportResult, import_xmi_file


class ActivitySyncResult(BaseModel):
    """Result of generating and importing one standalone activity."""

    function_name: str
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
    ) -> ActivitySyncResult:
        """Validate, generate, and import a standalone UML Activity package."""
        validate_activity_graph(graph)
        xmi_path = generate_activity_xmi(function_spec, graph, self.output_dir)
        import_result = import_xmi_file(
            xmi_path,
            self.toolkit_bat,
            self.log_dir,
            self.timeout,
        )
        return ActivitySyncResult(
            function_name=function_spec.name,
            xmi_path=xmi_path,
            import_result=import_result,
        )
