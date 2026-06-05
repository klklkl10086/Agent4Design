"""Read and write C-oriented Rhapsody model elements through the COM API."""

from __future__ import annotations

from typing import Any, Literal

from win32com.client import CastTo

from agent4design.domain.models import (
    CTypeInfo,
    ElementSummary,
    FunctionArgument,
    FunctionSpec,
    MacroSpec,
    StrictModel,
    VariableSpec,
)
from agent4design.rhapsody.com_runtime import run_on_com
from agent4design.rhapsody.context import RhapsodyContext, rhapsody_context
from agent4design.rhapsody.type_registry import TypeRegistry
from agent4design.tools.tool import sanitize_identifier


class ModelConflictError(RuntimeError):
    """Raised when an existing element has the requested name but a different type."""


class UnknownTypeError(LookupError):
    """Raised when a custom type is absent and placeholder creation is disabled."""


class UnsupportedTargetError(RuntimeError):
    """Raised when the selected Rhapsody target cannot accept a write."""


SyncElementKind = Literal["macro", "variable", "function"]
TypeResolutionKind = Literal[
    "void",
    "reuse",
    "text_declaration",
    "placeholder",
    "missing",
]


class TypeResolutionSummary(StrictModel):
    """Serializable dry-run result for one C type reference."""

    name: str
    resolution: TypeResolutionKind
    element: ElementSummary | None = None
    message: str = ""


class OperationReference(StrictModel):
    """XMI-ready reference to an existing Rhapsody Operation."""

    name: str
    path: str = ""
    guid: str = ""
    xmi_id: str = ""
    container_name: str = ""
    container_meta_class: str = ""
    container_path: str = ""
    container_guid: str = ""
    container_xmi_id: str = ""


BUILTIN_C_TYPES = {
    "_Bool",
    "char",
    "double",
    "float",
    "int",
    "long",
    "long double",
    "long int",
    "long long",
    "long long int",
    "short",
    "short int",
    "signed",
    "signed char",
    "signed int",
    "signed long",
    "signed long int",
    "signed long long",
    "signed long long int",
    "signed short",
    "signed short int",
    "unsigned",
    "unsigned char",
    "unsigned int",
    "unsigned long",
    "unsigned long int",
    "unsigned long long",
    "unsigned long long int",
    "unsigned short",
    "unsigned short int",
}


INTERFACE_BY_META_CLASS = {
    "Attribute": "IRPAttribute",
    "Variable": "IRPVariable",
    "Operation": "IRPOperation",
    "Function": "IRPOperation",
    "Argument": "IRPArgument",
    "Type": "IRPType",
}

FUNCTION_TARGET_META_CLASSES = ("Class", "Block")


def _cast_to_specific_interface(element: Any, meta_class: str) -> Any:
    interface_name = INTERFACE_BY_META_CLASS.get(meta_class)
    if interface_name is None:
        return element
    try:
        return CastTo(element, interface_name)
    except Exception:
        return element


def _element_path(element: Any) -> str:
    try:
        return element.getFullPathName()
    except Exception:
        return getattr(element, "name", "")


def _element_summary(element: Any, created: bool) -> ElementSummary:
    return ElementSummary(
        name=getattr(element, "name", ""),
        meta_class=getattr(element, "metaClass", ""),
        path=_element_path(element),
        created=created,
    )


def _element_guid(element: Any) -> str:
    try:
        return str(getattr(element, "GUID", "") or "")
    except Exception:
        return ""


def _guid_to_xmi_id(guid: str) -> str:
    value = (guid or "").strip()
    if not value:
        return ""
    if value.startswith("GUID "):
        value = value[5:].strip()
    if value.startswith("GUID+"):
        value = value[5:].strip()
    value = value.strip("{}")
    if value.endswith("_0"):
        value = value[:-2]
    return f"GUID+{value}"


def _is_writable_container(element: Any) -> bool:
    try:
        if element.isReadOnly():
            return False
    except Exception:
        pass
    try:
        save_unit = element.getSaveUnit()
        if save_unit is not None and save_unit.isReadOnly():
            return False
    except Exception:
        pass
    try:
        if element.getCMState() not in (0,):
            return False
    except Exception:
        pass
    return True


def _find_child(target: Any, meta_class: str, name: str) -> Any | None:
    collection = target.getNestedElementsByMetaClass(meta_class, 0)
    if collection is None:
        return None
    for index in range(1, collection.Count + 1):
        item = collection.Item(index)
        if getattr(item, "name", "") == name:
            return _cast_to_specific_interface(item, meta_class)
    return None


def _find_named_child(target: Any, name: str) -> Any | None:
    collection = target.getNestedElements()
    if collection is None:
        return None
    for index in range(1, collection.Count + 1):
        item = collection.Item(index)
        if getattr(item, "name", "") == name:
            return item
    return None


def _get_or_create_element(target: Any, meta_class: str, name: str) -> tuple[Any, bool]:
    existing = _find_child(target, meta_class, name)
    if existing is not None:
        return existing, False

    conflict = _find_named_child(target, name)
    if conflict is not None:
        conflict_meta = getattr(conflict, "metaClass", "Unknown")
        raise ModelConflictError(
            f"Element '{name}' already exists as {conflict_meta}; expected {meta_class}"
        )
    if not _is_writable_container(target):
        raise PermissionError(f"Rhapsody container is read-only: {_element_path(target)}")

    element = target.addNewAggr(meta_class, name)
    if element is None:
        raise RuntimeError(f"Rhapsody failed to create {meta_class} '{name}'")
    return _cast_to_specific_interface(element, meta_class), True


def _get_or_create_argument(operation: Any, argument: FunctionArgument) -> Any:
    try:
        collection = operation.arguments
        for index in range(1, collection.Count + 1):
            item = collection.Item(index)
            if getattr(item, "name", "") == argument.name:
                return _cast_to_specific_interface(item, "Argument")
    except Exception:
        pass

    item = operation.addArgument(argument.name)
    if item is None:
        raise RuntimeError(f"Rhapsody failed to create argument '{argument.name}'")
    return _cast_to_specific_interface(item, "Argument")


def get_sync_meta_class(target: Any, kind: SyncElementKind) -> str:
    """Select the Rhapsody metaclass used for one semantic C element."""
    target_meta_class = getattr(target, "metaClass", "")
    if kind == "function":
        if target_meta_class not in FUNCTION_TARGET_META_CLASSES:
            raise UnsupportedTargetError(
                "Function synchronization requires the selected Rhapsody target "
                "to be a Class or Block. The current target metaClass is "
                f"'{target_meta_class or 'Unknown'}'. Select a Class in Rhapsody, "
                "then run select_rhapsody_target or initialize_rhapsody again."
            )
        return "Operation"

    is_classifier = target_meta_class in ("Class", "Block")
    return "Attribute" if is_classifier else "Variable"


def _set_if_supported(element: Any, attribute: str, value: Any) -> None:
    try:
        setattr(element, attribute, value)
    except Exception:
        pass


class RhapsodyRepository:
    """Map validated domain models to Rhapsody COM operations."""

    def __init__(
        self,
        context: RhapsodyContext = rhapsody_context,
        type_registry: TypeRegistry | None = None,
        *,
        create_placeholder_type: bool = False,
    ) -> None:
        self.context = context
        self.type_registry = type_registry or TypeRegistry(context)
        self.create_placeholder_type = create_placeholder_type

    def _find_or_create_type_in_thread(self, type_info: CTypeInfo) -> tuple[Any | None, bool]:
        base_type = type_info.base_type.strip()
        if not base_type or base_type == "void":
            return None, False

        self.context.ensure_connection_in_thread()
        classifier = self.type_registry._resolve_in_thread(
            base_type,
            prefer_profile=base_type in BUILTIN_C_TYPES,
        )
        if classifier is not None:
            return classifier, False

        if base_type in BUILTIN_C_TYPES:
            return None, False
        if not self.create_placeholder_type:
            raise UnknownTypeError(
                f"Unknown custom type '{base_type}'. Add it to the Rhapsody project "
                "or construct RhapsodyRepository(create_placeholder_type=True)."
            )

        target = self.context.require_target_in_thread()
        classifier, created = _get_or_create_element(target, "Type", base_type)
        _set_if_supported(classifier, "kind", "Language")
        _set_if_supported(classifier, "declaration", base_type)
        self.type_registry._refresh_in_thread()
        return classifier, created

    def find_or_create_type(self, type_info: CTypeInfo) -> ElementSummary | None:
        """Find a project type, optionally creating a configured placeholder."""
        def _impl() -> ElementSummary | None:
            classifier, created = self._find_or_create_type_in_thread(type_info)
            if classifier is None:
                return None
            return _element_summary(classifier, created)

        return run_on_com(_impl)

    def inspect_type_reference(self, type_info: CTypeInfo) -> TypeResolutionSummary:
        """Describe type handling without creating a placeholder."""
        def _impl() -> TypeResolutionSummary:
            base_type = type_info.base_type.strip()
            if not base_type or base_type == "void":
                return TypeResolutionSummary(name=base_type or "void", resolution="void")

            self.context.ensure_connection_in_thread()
            classifier = self.type_registry._resolve_in_thread(
                base_type,
                prefer_profile=base_type in BUILTIN_C_TYPES,
            )
            if classifier is not None:
                return TypeResolutionSummary(
                    name=base_type,
                    resolution="reuse",
                    element=_element_summary(classifier, False),
                )
            if base_type in BUILTIN_C_TYPES:
                return TypeResolutionSummary(
                    name=base_type,
                    resolution="text_declaration",
                    message="Built-in C type will use a textual declaration.",
                )
            if self.create_placeholder_type:
                return TypeResolutionSummary(
                    name=base_type,
                    resolution="placeholder",
                    message="Unknown custom type will create a configured placeholder.",
                )
            return TypeResolutionSummary(
                name=base_type,
                resolution="missing",
                message="Unknown custom type; synchronization will be rejected.",
            )

        return run_on_com(_impl)

    def inspect_sync_target(
        self,
        kind: SyncElementKind,
        name: str,
    ) -> ElementSummary | None:
        """Locate the direct target element without modifying the model."""
        def _impl() -> ElementSummary | None:
            target = self.context.require_target_in_thread()
            meta_class = get_sync_meta_class(target, kind)
            sanitized_name = sanitize_identifier(name)
            existing = _find_child(target, meta_class, sanitized_name)
            if existing is not None:
                return _element_summary(existing, False)

            conflict = _find_named_child(target, sanitized_name)
            if conflict is not None:
                conflict_meta = getattr(conflict, "metaClass", "Unknown")
                raise ModelConflictError(
                    f"Element '{sanitized_name}' already exists as {conflict_meta}; "
                    f"expected {meta_class}"
                )
            return None

        return run_on_com(_impl)

    def resolve_operation_reference(self, name: str) -> OperationReference:
        """Return the selected target's Operation GUID in Rhapsody XMI form."""
        def _impl() -> OperationReference:
            target = self.context.require_target_in_thread()
            operation = _find_child(target, "Operation", sanitize_identifier(name))
            if operation is None:
                raise LookupError(
                    f"Operation '{sanitize_identifier(name)}' was not found "
                    "under the selected Rhapsody target."
                )
            guid = _element_guid(operation)
            xmi_id = _guid_to_xmi_id(guid)
            if not xmi_id:
                raise RuntimeError(
                    f"Operation '{sanitize_identifier(name)}' does not expose a GUID."
                )
            container = getattr(operation, "owner", None) or target
            container_guid = _element_guid(container)
            container_xmi_id = _guid_to_xmi_id(container_guid)
            return OperationReference(
                name=getattr(operation, "name", sanitize_identifier(name)),
                path=_element_path(operation),
                guid=guid,
                xmi_id=xmi_id,
                container_name=getattr(container, "name", ""),
                container_meta_class=getattr(container, "metaClass", ""),
                container_path=_element_path(container),
                container_guid=container_guid,
                container_xmi_id=container_xmi_id,
            )

        return run_on_com(_impl)

    def _assign_type_in_thread(
        self,
        element: Any,
        type_info: CTypeInfo,
        element_meta_class: str,
        *,
        is_return: bool = False,
    ) -> None:
        classifier, _ = self._find_or_create_type_in_thread(type_info)
        base_type = type_info.base_type.strip()
        textual_type = f"{'const ' if type_info.is_const else ''}{base_type} {type_info.pointer_modifier}".strip()

        if classifier is not None:
            classifier_meta = getattr(classifier, "metaClass", "")
            if is_return:
                if classifier_meta == "Type":
                    element.returnType = classifier
                else:
                    element.returns = classifier
            else:
                if classifier_meta == "Type":
                    element.typeOf = classifier
                else:
                    element.type = classifier

        if type_info.array_multiplicity and not is_return:
            if element_meta_class == "Attribute":
                element.multiplicity = type_info.array_multiplicity
            try:
                element.setPropertyValue(
                    f"C_CG::{element_meta_class}::Array",
                    f"[{type_info.array_multiplicity}]",
                )
            except Exception:
                pass

        if type_info.is_const:
            if element_meta_class == "Operation":
                element.isConst = True
            elif element_meta_class == "Attribute":
                element.isConstant = True
        if type_info.is_static and element_meta_class in ("Operation", "Attribute"):
            element.isStatic = True

        if is_return:
            element.setReturnTypeDeclaration(textual_type)
        else:
            element.setTypeDeclaration(textual_type)

    def sync_function(self, spec: FunctionSpec) -> ElementSummary:
        """Create or update a Function or Operation and its ordered arguments."""
        def _impl() -> ElementSummary:
            target = self.context.require_target_in_thread()
            meta_class = get_sync_meta_class(target, "function")
            operation, created = _get_or_create_element(
                target,
                meta_class,
                sanitize_identifier(spec.name),
            )

            self._assign_type_in_thread(
                operation,
                spec.return_type_info,
                meta_class,
                is_return=True,
            )
            for argument in spec.arguments:
                if argument.type_info.base_type.strip() == "void":
                    continue
                argument_element = _get_or_create_argument(operation, argument)
                self._assign_type_in_thread(argument_element, argument.type_info, "Argument")

            return _element_summary(operation, created)

        return run_on_com(_impl)

    def sync_variable(self, spec: VariableSpec) -> ElementSummary:
        """Create or update a Variable or Attribute."""
        def _impl() -> ElementSummary:
            target = self.context.require_target_in_thread()
            meta_class = get_sync_meta_class(target, "variable")
            variable, created = _get_or_create_element(
                target,
                meta_class,
                sanitize_identifier(spec.name),
            )
            self._assign_type_in_thread(variable, spec.type_info, meta_class)
            if spec.initial_value is not None:
                _set_if_supported(variable, "defaultValue", spec.initial_value.strip(" =;"))
            return _element_summary(variable, created)

        return run_on_com(_impl)

    def sync_macro(self, spec: MacroSpec) -> ElementSummary:
        """Create or update the current Rhapsody representation of a C macro."""
        def _impl() -> ElementSummary:
            target = self.context.require_target_in_thread()
            meta_class = get_sync_meta_class(target, "macro")
            macro, created = _get_or_create_element(
                target,
                meta_class,
                sanitize_identifier(spec.name),
            )
            try:
                macro.addStereotype("Define", meta_class)
            except Exception:
                pass
            _set_if_supported(macro, "defaultValue", spec.value.strip(" =;"))
            if spec.type_info is not None:
                self._assign_type_in_thread(macro, spec.type_info, meta_class)
            return _element_summary(macro, created)

        return run_on_com(_impl)


rhapsody_repository = RhapsodyRepository()
