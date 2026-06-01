"""Serializable state contract for the optional LangGraph workflow."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class SyncWorkflowState(TypedDict, total=False):
    """LangGraph state containing JSON-compatible values only."""

    request: Dict[str, Any]
    select_current_target: bool
    context: Dict[str, str]
    type_registry: Dict[str, int]
    plan: Dict[str, Any]
    write_approved: bool
    model_sync: Dict[str, Any]
    activities: List[Dict[str, Any]]
    verification: Dict[str, Any]
    save_approved: bool
    saved: bool
    success: bool
    errors: List[str]
