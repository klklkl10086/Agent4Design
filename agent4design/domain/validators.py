"""Validation helpers for domain models."""

from agent4design.domain.models import ActivityGraph


class ActivityGraphValidationError(ValueError):
    """Raised when an activity graph cannot be converted to XMI safely."""


def validate_activity_graph(graph: ActivityGraph) -> None:
    """Validate graph structure before generating an XMI artifact."""
    if not graph.nodes:
        raise ActivityGraphValidationError("Activity graph must contain at least one node.")

    node_ids = [node.id for node in graph.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ActivityGraphValidationError("Activity graph node ids must be unique.")

    initial_count = sum(node.type == "Initial" for node in graph.nodes)
    final_count = sum(node.type == "Final" for node in graph.nodes)
    if initial_count != 1:
        raise ActivityGraphValidationError("Activity graph must contain exactly one Initial node.")
    if final_count != 1:
        raise ActivityGraphValidationError("Activity graph must contain exactly one Final node.")

    known_ids = set(node_ids)
    for edge in graph.edges:
        if edge.source not in known_ids:
            raise ActivityGraphValidationError(f"Unknown edge source: {edge.source}")
        if edge.target not in known_ids:
            raise ActivityGraphValidationError(f"Unknown edge target: {edge.target}")
