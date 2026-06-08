"""Generate UML 2.1 activity XMI artifacts from validated activity graphs."""

from pathlib import Path
from typing import Dict, Set, Union
import uuid
import xml.etree.ElementTree as ET

from agent4design.domain.models import ActivityGraph, FunctionSpec
from agent4design.domain.validators import validate_activity_graph
from agent4design.tools.tool import sanitize_identifier


XMI_NS = "http://schema.omg.org/spec/XMI/2.1"
UML_NS = "http://schema.omg.org/spec/UML/2.1"
RHP_NS = "http://RhapsodyStandardModel/schemas/RhapsodyProfile/_FZBiUGCaEfGfBrKpKCTvvw/0"

ET.register_namespace("xmi", XMI_NS)
ET.register_namespace("uml", UML_NS)
ET.register_namespace("RhapsodyProfile", RHP_NS)


def _xmi(attribute: str) -> str:
    return f"{{{XMI_NS}}}{attribute}"


def _uml(element: str) -> str:
    return f"{{{UML_NS}}}{element}"


def _rhp(element: str) -> str:
    return f"{{{RHP_NS}}}{element}"


def _indent_xml(element: ET.Element, level: int = 0) -> None:
    """Indent XML on Python versions that do not provide ElementTree.indent."""
    indentation = "\n" + level * "  "
    child_indentation = "\n" + (level + 1) * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = child_indentation
        for child in element:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indentation
    elif level and (not element.tail or not element.tail.strip()):
        element.tail = indentation


def _new_xmi_id(label: str = "") -> str:
    suffix = f"_{sanitize_identifier(label)}" if label else ""
    return f"GUID+{uuid.uuid4()}{suffix}"


def _rhapsody_guid(xmi_id: str) -> str:
    value = (xmi_id or "").strip()
    if value.startswith("GUID+"):
        return f"GUID {value[5:]}"
    return value.replace("GUID+", "GUID ", 1)


def _build_xmi_node_ids(graph: ActivityGraph) -> Dict[str, str]:
    """Build Rhapsody-style XMI ids and reject source node id collisions."""
    result: Dict[str, str] = {}
    seen_source_ids: Set[str] = set()

    for node in graph.nodes:
        if node.id in seen_source_ids:
            raise ValueError(f"Duplicate node id: {node.id}")
        result[node.id] = (
            _new_xmi_id("InitialNode")
            if node.type == "Initial"
            else _new_xmi_id()
        )
        seen_source_ids.add(node.id)

    return result


def _edge_ids_by_node(
    graph: ActivityGraph,
    edge_ids: Dict[int, str],
) -> tuple[Dict[str, list[str]], Dict[str, list[str]]]:
    incoming: Dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    outgoing: Dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for index, edge in enumerate(graph.edges):
        outgoing[edge.source].append(edge_ids[index])
        incoming[edge.target].append(edge_ids[index])
    return incoming, outgoing


def _add_activity_node(
    activity: ET.Element,
    *,
    activity_id: str,
    node_id: str,
    node_type: str,
    name: str,
    description: str,
    incoming: list[str],
    outgoing: list[str],
) -> None:
    xmi_type_by_node_type = {
        "Action": "uml:OpaqueAction",
        "Decision": "uml:DecisionNode",
        "Merge": "uml:MergeNode",
        "Initial": "uml:InitialNode",
        "Final": "uml:ActivityFinalNode",
    }
    attributes = {
        _xmi("type"): xmi_type_by_node_type[node_type],
        _xmi("id"): node_id,
        "activity": activity_id,
    }
    if name:
        attributes["name"] = name
    if incoming:
        attributes["incoming"] = " ".join(incoming)
    if outgoing:
        attributes["outgoing"] = " ".join(outgoing)

    node = ET.SubElement(
        activity,
        "node",
        attributes,
    )
    if node_type == "Action" and description:
        ET.SubElement(node, "body").text = description


def _add_action_description(root: ET.Element, *, action_id: str, description: str) -> None:
    if not description:
        return
    ET.SubElement(
        root,
        _rhp("RhpModelElement"),
        {
            _xmi("id"): f"{action_id}_Stereotype_RhapsodyProfile_RhpModelElement",
            "guid": _rhapsody_guid(action_id),
            "description": description,
            "base_OpaqueAction": action_id,
        },
    )


def _create_root_container(
    *,
    function_name: str,
    package_name: str,
) -> tuple[ET.Element, ET.Element]:
    root = ET.Element(_xmi("XMI"), {_xmi("version"): "2.1"})
    container = ET.SubElement(
        root,
        _uml("Package"),
        {
            _xmi("id"): _new_xmi_id("Package"),
            "name": sanitize_identifier(package_name) if package_name else f"{function_name}_Import",
        },
    )
    return root, container


def generate_activity_xmi(
    function_spec: FunctionSpec,
    graph: ActivityGraph,
    output_dir: Union[str, Path] = "xmi_read",
    *,
    operation_xmi_id: str = "",
    package_name: str = "",
    container_xmi_id: str = "",
    container_name: str = "",
    container_meta_class: str = "",
) -> str:
    """Generate an activity XMI file and return its absolute path."""
    validate_activity_graph(graph)

    function_name = sanitize_identifier(function_spec.name)
    node_ids = _build_xmi_node_ids(graph)
    edge_ids = {index: _new_xmi_id() for index, _ in enumerate(graph.edges)}
    incoming_by_node, outgoing_by_node = _edge_ids_by_node(graph, edge_ids)
    activity_id = _new_xmi_id()

    root, container = _create_root_container(
        function_name=function_name,
        package_name=package_name,
    )

    activity_attributes = {
        _xmi("type"): "uml:Activity",
        _xmi("id"): activity_id,
        "name": f"activity_{function_name}",
    }

    activity = ET.SubElement(
        container,
        "packagedElement",
        activity_attributes,
    )
    constraint_id = _new_xmi_id("Container")
    ET.SubElement(
        activity,
        "ownedRule",
        {
            _xmi("type"): "uml:Constraint",
            _xmi("id"): constraint_id,
            "name": "ActivityDiagram",
            "context": activity_id,
        },
    )

    for node in graph.nodes:
        _add_activity_node(
            activity,
            activity_id=activity_id,
            node_id=node_ids[node.id],
            node_type=node.type,
            name=node.label or f"{node.type}_{sanitize_identifier(node.id)}",
            description=node.description,
            incoming=incoming_by_node[node.id],
            outgoing=outgoing_by_node[node.id],
        )
        if node.type == "Action":
            _add_action_description(
                root,
                action_id=node_ids[node.id],
                description=node.description,
            )

    for index, edge in enumerate(graph.edges):
        edge_id = edge_ids[index]
        edge_element = ET.SubElement(
            activity,
            "edge",
            {
                _xmi("type"): "uml:ControlFlow",
                _xmi("id"): edge_id,
                "name": str(index),
                "source": node_ids[edge.source],
                "target": node_ids[edge.target],
                "activity": activity_id,
            },
        )
        if edge.guard:
            guard_value = edge.guard.strip()
            guard_type = (
                "uml:LiteralBoolean"
                if guard_value.lower() in ("true", "false")
                else "uml:LiteralString"
            )
            guard_attributes = {
                _xmi("type"): guard_type,
                _xmi("id"): f"{edge_id}_guard",
            }
            if guard_type == "uml:LiteralString":
                guard_attributes["value"] = edge.guard
            else:
                guard_attributes["value"] = guard_value.lower()
            ET.SubElement(
                edge_element,
                "guard",
                guard_attributes,
            )
        ET.SubElement(
            edge_element,
            "weight",
            {
                _xmi("type"): "uml:LiteralInteger",
                _xmi("id"): f"{edge_id}_weight",
                "value": "1",
            },
        )

    _indent_xml(root)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    xmi_path = (output_path / f"{function_name}.xmi").resolve()
    ET.ElementTree(root).write(xmi_path, encoding="utf-8", xml_declaration=True)
    return str(xmi_path)
