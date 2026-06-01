"""Construct the optional LangGraph workflow for Rhapsody synchronization."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from agent4design.services.agent_service import Agent4DesignService, agent4design_service
from agent4design.workflows.nodes import SyncWorkflowNodes
from agent4design.workflows.state import SyncWorkflowState


def _require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph support is optional. Install the 'graph' extra with "
            "`pip install -e .[graph]`."
        ) from exc
    return StateGraph, START, END, interrupt


def create_sqlite_checkpointer(path: Union[str, Path]):
    """Create a SQLite checkpointer for resumable approval workflows."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "SQLite persistence requires `pip install -e .[graph]`."
        ) from exc
    connection = sqlite3.connect(str(Path(path)), check_same_thread=False)
    return SqliteSaver(connection)


def build_sync_graph(
    service: Agent4DesignService = agent4design_service,
    *,
    checkpointer: Optional[Any] = None,
):
    """Build a resumable workflow with approval before writes and saves."""
    StateGraph, START, END, interrupt = _require_langgraph()
    nodes = SyncWorkflowNodes(service, interrupt)
    graph = StateGraph(SyncWorkflowState)

    graph.add_node("initialize", nodes.initialize)
    graph.add_node("refresh_types", nodes.refresh_types)
    graph.add_node("plan_sync", nodes.plan_sync)
    graph.add_node("request_write_approval", nodes.request_write_approval)
    graph.add_node("sync_model", nodes.sync_model)
    graph.add_node("sync_activities", nodes.sync_activities)
    graph.add_node("verify", nodes.verify)
    graph.add_node("request_save_approval", nodes.request_save_approval)
    graph.add_node("save_project", nodes.save_project)
    graph.add_node("summarize", nodes.summarize)

    graph.add_edge(START, "initialize")
    graph.add_conditional_edges(
        "initialize",
        lambda state: "summarize" if state.get("errors") else "refresh_types",
    )
    graph.add_conditional_edges(
        "refresh_types",
        lambda state: "summarize" if state.get("errors") else "plan_sync",
    )
    graph.add_conditional_edges(
        "plan_sync",
        lambda state: "summarize" if state.get("errors") else "request_write_approval",
    )
    graph.add_conditional_edges(
        "request_write_approval",
        lambda state: "summarize" if state.get("errors") else "sync_model",
    )
    graph.add_conditional_edges(
        "sync_model",
        lambda state: "summarize" if state.get("errors") else "sync_activities",
    )
    graph.add_conditional_edges(
        "sync_activities",
        lambda state: "summarize" if state.get("errors") else "verify",
    )
    graph.add_conditional_edges(
        "verify",
        lambda state: (
            "summarize"
            if state.get("errors")
            else "request_save_approval"
        ),
    )
    graph.add_conditional_edges(
        "request_save_approval",
        lambda state: (
            "summarize"
            if state.get("errors") or not state.get("save_approved")
            else "save_project"
        ),
    )
    graph.add_edge("save_project", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer)
