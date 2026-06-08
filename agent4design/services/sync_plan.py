"""Build a read-only synchronization plan before changing Rhapsody."""

from __future__ import annotations

from typing import List, Literal

from pydantic import Field

from agent4design.domain.models import CTypeInfo, StrictModel
from agent4design.rhapsody.repository import (
    RhapsodyRepository,
    SyncElementKind,
    TypeResolutionSummary,
    rhapsody_repository,
)
from agent4design.services.model_sync import ModelSyncRequest
from agent4design.tools.tool import sanitize_identifier


PlanAction = Literal["create", "update", "reject"]


class SyncPlanItem(StrictModel):
    """Dry-run decision for one semantic model element."""

    kind: SyncElementKind
    name: str
    action: PlanAction
    meta_class: str = ""
    type_resolutions: List[TypeResolutionSummary] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: str = ""


class ModelSyncPlan(StrictModel):
    """Serializable dry-run report suitable for an approval prompt."""

    success: bool
    requires_approval: bool
    items: List[SyncPlanItem] = Field(default_factory=list)


class SyncPlanService:
    """Inspect types and target elements without performing COM writes."""

    def __init__(self, repository: RhapsodyRepository = rhapsody_repository) -> None:
        self.repository = repository

    @staticmethod
    def _types_for(kind: SyncElementKind, spec: object) -> List[CTypeInfo]:
        if kind == "type":
            type_kind = getattr(spec, "kind", "")
            if type_kind in ("struct", "union"):
                return [
                    member.type_info
                    for member in getattr(spec, "attributes", [])
                ]
            if type_kind == "typedef":
                basic_type = getattr(spec, "basic_type", None)
                return [basic_type] if basic_type is not None else []
            return []
        if kind == "function":
            return_type = getattr(spec, "return_type_info")
            arguments = [argument.type_info for argument in getattr(spec, "arguments")]
            return [return_type, *arguments]
        type_info = getattr(spec, "type_info", None)
        return [type_info] if type_info is not None else []

    def _plan_item(
        self,
        kind: SyncElementKind,
        spec: object,
        known_type_names: set[str] | None = None,
    ) -> SyncPlanItem:
        name = getattr(spec, "name", "")
        known_type_names = known_type_names or set()
        type_resolutions = [
            self.repository.inspect_type_reference(type_info)
            for type_info in self._types_for(kind, spec)
        ]
        missing = [
            resolution.message
            for resolution in type_resolutions
            if resolution.resolution == "missing"
            and resolution.name not in known_type_names
        ]
        warnings = [
            resolution.message
            for resolution in type_resolutions
            if resolution.resolution in ("placeholder", "text_declaration")
            and resolution.message
        ]
        warnings.extend(
            f"Type '{resolution.name}' is defined in this request."
            for resolution in type_resolutions
            if resolution.resolution == "missing"
            and resolution.name in known_type_names
        )
        if missing:
            return SyncPlanItem(
                kind=kind,
                name=name,
                action="reject",
                type_resolutions=type_resolutions,
                warnings=warnings,
                error=" ".join(missing),
            )

        try:
            existing = self.repository.inspect_sync_target(kind, name)
        except Exception as exc:
            return SyncPlanItem(
                kind=kind,
                name=name,
                action="reject",
                type_resolutions=type_resolutions,
                warnings=warnings,
                error=str(exc),
            )

        return SyncPlanItem(
            kind=kind,
            name=name,
            action="update" if existing is not None else "create",
            meta_class=existing.meta_class if existing is not None else "",
            type_resolutions=type_resolutions,
            warnings=warnings,
        )

    def plan(self, request: ModelSyncRequest) -> ModelSyncPlan:
        """Plan types, macros, variables, and functions in execution order."""
        known_type_names = {
            spec.name for spec in request.types
        } | {
            sanitize_identifier(spec.name) for spec in request.types
        }
        items = [
            *[
                self._plan_item("type", spec, known_type_names)
                for spec in request.types
            ],
            *[
                self._plan_item("macro", spec, known_type_names)
                for spec in request.macros
            ],
            *[
                self._plan_item("variable", spec, known_type_names)
                for spec in request.variables
            ],
            *[
                self._plan_item("function", spec, known_type_names)
                for spec in request.functions
            ],
        ]
        success = all(item.action != "reject" for item in items)
        return ModelSyncPlan(
            success=success,
            requires_approval=any(item.action in ("create", "update") for item in items),
            items=items,
        )


sync_plan_service = SyncPlanService()
