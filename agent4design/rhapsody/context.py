"""Manage the active Rhapsody application, project, and selected model container."""

from __future__ import annotations

from typing import Any, Dict

from win32com.client import gencache

from agent4design.rhapsody.com_runtime import run_on_com


VALID_CONTAINERS = ("Project", "Package", "Class", "Block", "File", "Module")


def get_project_key(project: Any) -> str:
    """Build a stable active-project key without retaining a COM reference."""
    try:
        guid = getattr(project, "GUID", "")
        if guid:
            return f"GUID:{guid}"
    except Exception:
        pass
    try:
        return f"PATH:{project.getFullPathName()}"
    except Exception:
        return f"NAME:{getattr(project, 'name', '')}"


def get_effective_target(element: Any) -> Any:
    """Walk upward until an element suitable for C model content is found."""
    current = element
    while current and getattr(current, "metaClass", "") not in VALID_CONTAINERS:
        owner = getattr(current, "owner", None)
        if owner is None:
            break
        current = owner
    return current or element


class RhapsodyContext:
    """Hold COM references that must only be used through the COM dispatcher."""

    def __init__(self) -> None:
        self.app = None
        self.project = None
        self.target = None
        self.project_key = ""
        self.project_name = ""
        self.target_name = ""
        self.target_meta_class = ""
        self.target_path = ""

    def _clear_target_in_thread(self) -> None:
        self.target = None
        self.target_name = ""
        self.target_meta_class = ""
        self.target_path = ""

    def _connect_in_thread(self) -> None:
        self.app = gencache.EnsureDispatch("Rhapsody2.Application")
        self.project = self.app.activeProject()
        if self.project is None:
            raise RuntimeError("No active Rhapsody project is open")
        self._clear_target_in_thread()
        self.project_key = get_project_key(self.project)
        self.project_name = getattr(self.project, "name", "")

    def connect(self) -> None:
        """Connect to Rhapsody and load the active project."""
        run_on_com(self._connect_in_thread)

    def ensure_connection_in_thread(self) -> None:
        """Reconnect when the stored COM application reference is stale."""
        try:
            if self.app is None:
                raise RuntimeError("Rhapsody application is not connected")
            project = self.app.activeProject()
            if project is None:
                raise RuntimeError("No active Rhapsody project is open")
            project_key = get_project_key(project)
            if project_key != self.project_key:
                self._clear_target_in_thread()
            self.project = project
            self.project_key = project_key
            self.project_name = getattr(project, "name", "")
        except Exception:
            self._clear_target_in_thread()
            self._connect_in_thread()

    def _select_current_target_in_thread(self) -> None:
        self.ensure_connection_in_thread()
        selected = self.app.getSelectedElement()
        if selected is None:
            raise RuntimeError("Select a target element in Rhapsody first")

        self.target = get_effective_target(selected)
        self.target_name = getattr(self.target, "name", "")
        self.target_meta_class = getattr(self.target, "metaClass", "")
        try:
            self.target_path = self.target.getFullPathName()
        except Exception:
            self.target_path = self.target_name

    def select_current_target(self) -> None:
        """Read the GUI selection and resolve its writable model container."""
        run_on_com(self._select_current_target_in_thread)

    def initialize(self) -> None:
        """Connect to Rhapsody and capture the current GUI selection."""
        run_on_com(self._connect_in_thread)
        run_on_com(self._select_current_target_in_thread)

    def require_target_in_thread(self) -> Any:
        """Return a usable selected target while already on the COM thread."""
        self.ensure_connection_in_thread()
        if self.target is None:
            self._select_current_target_in_thread()
        return self.target

    def save_project(self) -> None:
        """Save the active Rhapsody project."""
        def _save() -> None:
            self.ensure_connection_in_thread()
            self.project.save()

        run_on_com(_save)

    def get_summary(self) -> Dict[str, str]:
        """Return cached strings safe to use outside the COM thread."""
        return {
            "project_name": self.project_name,
            "target_name": self.target_name,
            "target_meta_class": self.target_meta_class,
            "target_path": self.target_path,
        }


rhapsody_context = RhapsodyContext()
