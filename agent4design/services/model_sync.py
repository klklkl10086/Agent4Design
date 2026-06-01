"""Synchronize semantic C model elements through the Rhapsody repository."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import Field

from agent4design.domain.models import (
    ElementSummary,
    FunctionSpec,
    MacroSpec,
    StrictModel,
    VariableSpec,
)
from agent4design.rhapsody.context import RhapsodyContext, rhapsody_context
from agent4design.rhapsody.repository import RhapsodyRepository, rhapsody_repository


ElementKind = Literal["macro", "variable", "function"]


class ElementSyncResult(StrictModel):
    """Result of one semantic element synchronization attempt."""

    kind: ElementKind
    name: str
    success: bool
    element: Optional[ElementSummary] = None
    error: str = ""


class ModelSyncRequest(StrictModel):
    """Semantic C elements to synchronize in deterministic order."""

    macros: List[MacroSpec] = Field(default_factory=list)
    variables: List[VariableSpec] = Field(default_factory=list)
    functions: List[FunctionSpec] = Field(default_factory=list)
    continue_on_error: bool = True
    save_project: bool = False


class ModelSyncResult(StrictModel):
    """Structured report for a semantic model synchronization batch."""

    success: bool
    items: List[ElementSyncResult] = Field(default_factory=list)
    saved: bool = False
    save_error: str = ""


class ModelSyncService:
    """Expose repository operations as an observable application use case."""

    def __init__(
        self,
        repository: RhapsodyRepository = rhapsody_repository,
        context: RhapsodyContext = rhapsody_context,
    ) -> None:
        self.repository = repository
        self.context = context

    def sync_macro(self, spec: MacroSpec) -> ElementSummary:
        """Synchronize one macro representation."""
        return self.repository.sync_macro(spec)

    def sync_variable(self, spec: VariableSpec) -> ElementSummary:
        """Synchronize one C variable or class attribute."""
        return self.repository.sync_variable(spec)

    def sync_function(self, spec: FunctionSpec) -> ElementSummary:
        """Synchronize one C function or class operation."""
        return self.repository.sync_function(spec)

    def _sync_item(self, kind: ElementKind, spec: object) -> ElementSyncResult:
        name = getattr(spec, "name", "")
        try:
            if kind == "macro":
                element = self.sync_macro(spec)  # type: ignore[arg-type]
            elif kind == "variable":
                element = self.sync_variable(spec)  # type: ignore[arg-type]
            else:
                element = self.sync_function(spec)  # type: ignore[arg-type]
            return ElementSyncResult(
                kind=kind,
                name=name,
                success=True,
                element=element,
            )
        except Exception as exc:
            return ElementSyncResult(
                kind=kind,
                name=name,
                success=False,
                error=str(exc),
            )

    def sync(self, request: ModelSyncRequest) -> ModelSyncResult:
        """Synchronize macros, variables, then functions and optionally save."""
        items: List[ElementSyncResult] = []
        ordered_specs = (
            [("macro", spec) for spec in request.macros]
            + [("variable", spec) for spec in request.variables]
            + [("function", spec) for spec in request.functions]
        )

        for kind, spec in ordered_specs:
            item = self._sync_item(kind, spec)  # type: ignore[arg-type]
            items.append(item)
            if not item.success and not request.continue_on_error:
                break

        success = all(item.success for item in items)
        saved = False
        save_error = ""
        if request.save_project and success:
            try:
                self.context.save_project()
                saved = True
            except Exception as exc:
                success = False
                save_error = str(exc)

        return ModelSyncResult(
            success=success,
            items=items,
            saved=saved,
            save_error=save_error,
        )


model_sync_service = ModelSyncService()
