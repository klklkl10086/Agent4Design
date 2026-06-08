"""Read-only post-sync verification through the Rhapsody COM API."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal

from pydantic import Field

from agent4design.domain.models import (
    CTypeInfo,
    FunctionSpec,
    MacroSpec,
    StrictModel,
    TypeDefinitionSpec,
    VariableSpec,
)
from agent4design.rhapsody.com_runtime import run_on_com
from agent4design.rhapsody.context import RhapsodyContext, rhapsody_context
from agent4design.rhapsody.repository import (
    _element_path,
    _find_child,
    _resolve_classifier_target,
    _type_kind_matches,
    get_sync_meta_class,
)
from agent4design.tools.tool import sanitize_identifier


VerificationKind = Literal["type", "macro", "variable", "function", "activity"]


class VerificationItem(StrictModel):
    """Result of checking one expected Rhapsody element."""

    kind: VerificationKind
    name: str
    success: bool
    path: str = ""
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    details: Dict[str, str] = Field(default_factory=dict)


class VerificationReport(StrictModel):
    """Combined read-only verification report."""

    success: bool
    items: List[VerificationItem] = Field(default_factory=list)


def _normalize_declaration(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _expected_declaration(type_info: CTypeInfo) -> str:
    return _normalize_declaration(
        f"{'const ' if type_info.is_const else ''}"
        f"{type_info.base_type.strip()} {type_info.pointer_modifier}"
    )


class RhapsodyVerifier:
    """Check model state without retaining COM objects outside the STA thread."""

    def __init__(self, context: RhapsodyContext = rhapsody_context) -> None:
        self.context = context

    @staticmethod
    def _declaration(element: Any) -> str:
        return _normalize_declaration(getattr(element, "declaration", ""))

    def _verify_variable_in_thread(
        self,
        spec: VariableSpec,
        *,
        kind: Literal["macro", "variable"] = "variable",
    ) -> VerificationItem:
        target = self.context.require_target_in_thread()
        meta_class = get_sync_meta_class(target, kind)
        element = _find_child(target, meta_class, sanitize_identifier(spec.name))
        if element is None:
            return VerificationItem(
                kind=kind,
                name=spec.name,
                success=False,
                errors=[f"{meta_class} was not found under the selected target."],
            )

        errors = []
        expected = _expected_declaration(spec.type_info)
        actual = self._declaration(element)
        if actual and actual != expected:
            errors.append(f"Type declaration differs: expected '{expected}', got '{actual}'.")
        return VerificationItem(
            kind=kind,
            name=spec.name,
            success=not errors,
            path=_element_path(element),
            errors=errors,
            details={"meta_class": meta_class, "type_declaration": actual},
        )

    def _verify_macro_in_thread(self, spec: MacroSpec) -> VerificationItem:
        target = self.context.require_target_in_thread()
        meta_class = get_sync_meta_class(target, "macro")
        element = _find_child(target, meta_class, sanitize_identifier(spec.name))
        if element is None:
            return VerificationItem(
                kind="macro",
                name=spec.name,
                success=False,
                errors=[f"{meta_class} macro representation was not found."],
            )

        errors = []
        expected_value = spec.value.strip(" =;")
        actual_value = getattr(element, "defaultValue", "")
        if actual_value != expected_value:
            errors.append(
                f"Macro value differs: expected '{expected_value}', got '{actual_value}'."
            )
        if spec.type_info is not None:
            expected_type = _expected_declaration(spec.type_info)
            actual_type = self._declaration(element)
            if actual_type and actual_type != expected_type:
                errors.append(
                    f"Type declaration differs: expected '{expected_type}', got '{actual_type}'."
                )
        return VerificationItem(
            kind="macro",
            name=spec.name,
            success=not errors,
            path=_element_path(element),
            errors=errors,
            details={"meta_class": meta_class, "default_value": actual_value},
        )

    def _verify_function_in_thread(self, spec: FunctionSpec) -> VerificationItem:
        target = self.context.require_target_in_thread()
        meta_class = get_sync_meta_class(target, "function")
        operation = _find_child(target, meta_class, sanitize_identifier(spec.name))
        if operation is None:
            return VerificationItem(
                kind="function",
                name=spec.name,
                success=False,
                errors=[f"{meta_class} was not found under the selected target."],
            )

        errors = []
        expected_return = _expected_declaration(spec.return_type_info)
        try:
            actual_return = _normalize_declaration(operation.getReturnTypeDeclaration())
        except Exception:
            actual_return = ""
        if actual_return and actual_return != expected_return:
            errors.append(
                f"Return type differs: expected '{expected_return}', got '{actual_return}'."
            )

        actual_arguments = {}
        try:
            collection = operation.arguments
            for index in range(1, collection.Count + 1):
                argument = collection.Item(index)
                actual_arguments[getattr(argument, "name", "")] = self._declaration(argument)
        except Exception as exc:
            errors.append(f"Arguments could not be read: {exc}")

        for argument in spec.arguments:
            if argument.type_info.base_type.strip() == "void":
                continue
            actual_type = actual_arguments.get(argument.name)
            if actual_type is None:
                errors.append(f"Argument '{argument.name}' was not found.")
                continue
            expected_type = _expected_declaration(argument.type_info)
            if actual_type and actual_type != expected_type:
                errors.append(
                    f"Argument '{argument.name}' differs: expected '{expected_type}', "
                    f"got '{actual_type}'."
                )

        return VerificationItem(
            kind="function",
            name=spec.name,
            success=not errors,
            path=_element_path(operation),
            errors=errors,
            details={"meta_class": meta_class, "return_type": actual_return},
        )

    def _verify_type_in_thread(self, spec: TypeDefinitionSpec) -> VerificationItem:
        target = _resolve_classifier_target(
            self.context.require_target_in_thread(),
            "Type verification",
        )
        type_element = _find_child(target, "Type", sanitize_identifier(spec.name))
        if type_element is None:
            return VerificationItem(
                kind="type",
                name=spec.name,
                success=False,
                errors=["Type was not found under the selected target."],
            )

        warnings = []
        matches = _type_kind_matches(type_element, spec.kind)
        if matches is None:
            warnings.append("Type kind could not be read through the COM helper.")
        errors = []
        if matches is False:
            errors.append(f"Type kind differs from expected '{spec.kind}'.")

        return VerificationItem(
            kind="type",
            name=spec.name,
            success=not errors,
            path=_element_path(type_element),
            errors=errors,
            warnings=warnings,
            details={
                "meta_class": "Type",
                "kind": getattr(type_element, "kind", ""),
            },
        )

    def _verify_activity_in_thread(self, activity_name: str) -> VerificationItem:
        self.context.ensure_connection_in_thread()
        project = self.context.project
        for meta_class in ("Activity", "ActivityDiagram", "Flowchart"):
            element = project.findNestedElementRecursive(activity_name, meta_class)
            if element is not None:
                return VerificationItem(
                    kind="activity",
                    name=activity_name,
                    success=True,
                    path=_element_path(element),
                    warnings=[
                        "Activity existence was verified, but Function ownership remains experimental."
                    ],
                    details={"meta_class": meta_class},
                )
        return VerificationItem(
            kind="activity",
            name=activity_name,
            success=False,
            errors=["Imported activity was not found by supported experimental metaclasses."],
        )

    def verify(
        self,
        *,
        types: List[TypeDefinitionSpec] | None = None,
        macros: List[MacroSpec] | None = None,
        variables: List[VariableSpec] | None = None,
        functions: List[FunctionSpec] | None = None,
        activities: List[str] | None = None,
    ) -> VerificationReport:
        """Verify expected semantic elements and standalone activities."""
        def _impl() -> VerificationReport:
            items = [
                *[
                    self._verify_type_in_thread(spec)
                    for spec in (types or [])
                ],
                *[
                    self._verify_macro_in_thread(spec)
                    for spec in (macros or [])
                ],
                *[
                    self._verify_variable_in_thread(spec)
                    for spec in (variables or [])
                ],
                *[
                    self._verify_function_in_thread(spec)
                    for spec in (functions or [])
                ],
                *[
                    self._verify_activity_in_thread(name)
                    for name in (activities or [])
                ],
            ]
            return VerificationReport(
                success=all(item.success for item in items),
                items=items,
            )

        return run_on_com(_impl)


rhapsody_verifier = RhapsodyVerifier()
