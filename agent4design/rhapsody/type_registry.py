"""Serializable type metadata index for the active Rhapsody project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from pydantic import BaseModel

from agent4design.rhapsody.com_runtime import run_on_com
from agent4design.rhapsody.context import (
    RhapsodyContext,
    get_project_key,
    rhapsody_context,
)


class TypeReference(BaseModel):
    """Serializable metadata used to relocate a type in the current COM session."""

    name: str
    full_path: str
    meta_class: str


class AmbiguousTypeError(LookupError):
    """Raised when a short type name matches multiple project elements."""


class TypeRegistry:
    """Index Type and Class metadata without retaining COM objects."""

    def __init__(self, context: RhapsodyContext = rhapsody_context) -> None:
        self.context = context
        self._project_key = ""
        self._references: List[TypeReference] = []
        self._by_name: Dict[str, List[TypeReference]] = {}
        self._by_full_path: Dict[str, List[TypeReference]] = {}

    @property
    def references(self) -> List[TypeReference]:
        """Return a copy of the serializable metadata index."""
        return list(self._references)

    def _get_project_key_in_thread(self) -> str:
        self.context.ensure_connection_in_thread()
        return get_project_key(self.context.project)

    def _replace_index(self, references: List[TypeReference], project_key: str) -> None:
        self._references = references
        self._project_key = project_key
        self._by_name = {}
        self._by_full_path = {}
        for reference in references:
            self._by_name.setdefault(reference.name, []).append(reference)
            self._by_full_path.setdefault(reference.full_path, []).append(reference)

    @staticmethod
    def _to_reference(element: Any, fallback_meta_class: str) -> TypeReference:
        name = getattr(element, "name", "")
        try:
            full_path = element.getFullPathName()
        except Exception:
            full_path = name
        return TypeReference(
            name=name,
            full_path=full_path,
            meta_class=getattr(element, "metaClass", fallback_meta_class),
        )

    def _refresh_in_thread(self) -> None:
        project_key = self._get_project_key_in_thread()
        project = self.context.project
        references: List[TypeReference] = []
        seen = set()

        for meta_class in ("Type", "Class"):
            collection = project.getNestedElementsByMetaClass(meta_class, 1)
            if collection is None:
                continue
            for index in range(1, collection.Count + 1):
                reference = self._to_reference(collection.Item(index), meta_class)
                key = (reference.full_path, reference.meta_class)
                if key not in seen:
                    references.append(reference)
                    seen.add(key)

        self._replace_index(references, project_key)

    def refresh(self) -> None:
        """Scan Type and Class elements from the active project."""
        run_on_com(self._refresh_in_thread)

    def _ensure_current_project_in_thread(self) -> None:
        project_key = self._get_project_key_in_thread()
        if not self._references or project_key != self._project_key:
            self._refresh_in_thread()

    def _locate_in_thread(self, reference: TypeReference) -> Any | None:
        return self.context.project.findElementsByFullName(
            reference.full_path,
            reference.meta_class,
        )

    @staticmethod
    def _prefer_profile_reference(references: List[TypeReference]) -> TypeReference | None:
        profile_references = [
            reference
            for reference in references
            if "profile" in reference.full_path.lower()
        ]
        if len(profile_references) == 1:
            return profile_references[0]
        return None

    def _select_reference(
        self,
        name: str,
        *,
        prefer_profile: bool = False,
    ) -> TypeReference | None:
        references = self._by_full_path.get(name, [])
        if not references:
            references = self._by_name.get(name, [])
        if not references:
            return None
        if len(references) == 1:
            return references[0]

        if prefer_profile:
            profile_reference = self._prefer_profile_reference(references)
            if profile_reference is not None:
                return profile_reference

        paths = ", ".join(sorted(reference.full_path for reference in references))
        raise AmbiguousTypeError(
            f"Type '{name}' is ambiguous. Use a full path instead. Matches: {paths}"
        )

    def _resolve_in_thread(self, name: str, *, prefer_profile: bool = False) -> Any | None:
        self._ensure_current_project_in_thread()
        reference = self._select_reference(name, prefer_profile=prefer_profile)
        if reference is None:
            return None

        element = self._locate_in_thread(reference)
        if element is not None:
            return element

        # The model can change while the process is running. Rebuild once before
        # reporting a miss, and always relocate rather than retaining a COM proxy.
        self._refresh_in_thread()
        reference = self._select_reference(name, prefer_profile=prefer_profile)
        return self._locate_in_thread(reference) if reference is not None else None

    def resolve(self, name: str, *, prefer_profile: bool = False) -> Any | None:
        """Resolve a type to a COM object from the active Rhapsody session."""
        return run_on_com(
            lambda: self._resolve_in_thread(name, prefer_profile=prefer_profile)
        )

    def save_index(self, path: Union[str, Path]) -> None:
        """Save type metadata as JSON for diagnostics and later relocation."""
        run_on_com(self._ensure_current_project_in_thread)
        payload = {
            "project_key": self._project_key,
            "types": [reference.model_dump() for reference in self._references],
        }
        Path(path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_index(self, path: Union[str, Path]) -> None:
        """Load metadata only; COM objects are relocated when resolve is called."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        project_key = ""
        raw_references = payload
        if isinstance(payload, dict):
            project_key = payload.get("project_key", "")
            raw_references = payload.get("types", [])

        references = []
        for item in raw_references:
            if isinstance(item, dict):
                references.append(TypeReference.model_validate(item))
            else:
                full_path, meta_class = item
                references.append(
                    TypeReference(
                        name=str(full_path).split("::")[-1],
                        full_path=full_path,
                        meta_class=meta_class,
                    )
                )
        self._replace_index(references, project_key)


type_registry = TypeRegistry()
