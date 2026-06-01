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

ET.register_namespace("xmi", XMI_NS)
ET.register_namespace("uml", UML_NS)


def _xmi(attribute: str) -> str:
    return f"{{{XMI_NS}}}{attribute}"


def _uml(element: str) -> str:
    return f"{{{UML_NS}}}{element}"


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


def _build_xmi_node_ids(graph: ActivityGraph) -> Dict[str, str]:
    """Build XMI ids and reject collisions introduced by sanitization."""
    result: Dict[str, str] = {}
    used_ids: Set[str] = set()

    for node in graph.nodes:
        xmi_id = f"Node_{sanitize_identifier(node.id)}"
        if xmi_id in used_ids:
            raise ValueError(f"Node ids collide after sanitization: {node.id}")
        result[node.id] = xmi_id
        used_ids.add(xmi_id)

    return result


def _add_activity_node(activity: ET.Element, node_id: str, node_type: str, name: str, description: str) -> None:
    xmi_type_by_node_type = {
        "Action": "uml:OpaqueAction",
        "Decision": "uml:DecisionNode",
        "Merge": "uml:MergeNode",
        "Initial": "uml:InitialNode",
        "Final": "uml:ActivityFinalNode",
    }
    node = ET.SubElement(
        activity,
        "node",
        {
            _xmi("type"): xmi_type_by_node_type[node_type],
            _xmi("id"): node_id,
            "name": name,
        },
    )
    if node_type == "Action":
        ET.SubElement(node, "body").text = description


def generate_activity_xmi(
    function_spec: FunctionSpec,
    graph: ActivityGraph,
    output_dir: Union[str, Path] = "xmi_read",
) -> str:
    """Generate an activity XMI file and return its absolute path."""
    validate_activity_graph(graph)

    function_name = sanitize_identifier(function_spec.name)
    node_ids = _build_xmi_node_ids(graph)
    unique_suffix = uuid.uuid4().hex

    root = ET.Element(_xmi("XMI"), {_xmi("version"): "2.1"})
    package = ET.SubElement(
        root,
        _uml("Package"),
        {
            _xmi("id"): f"Package_{unique_suffix}",
            "name": function_name,
        },
    )
    activity = ET.SubElement(
        package,
        "packagedElement",
        {
            _xmi("type"): "uml:Activity",
            _xmi("id"): f"Activity_{unique_suffix}",
            "name": f"activity_{function_name}",
        },
    )

    for node in graph.nodes:
        _add_activity_node(
            activity,
            node_ids[node.id],
            node.type,
            node.label or f"{node.type}_{sanitize_identifier(node.id)}",
            node.description,
        )

    for index, edge in enumerate(graph.edges):
        edge_id = f"Edge_{index}_{uuid.uuid4().hex[:8]}"
        edge_element = ET.SubElement(
            activity,
            "edge",
            {
                _xmi("type"): "uml:ControlFlow",
                _xmi("id"): edge_id,
                "name": str(index),
                "source": node_ids[edge.source],
                "target": node_ids[edge.target],
            },
        )
        if edge.guard:
            ET.SubElement(
                edge_element,
                "guard",
                {
                    _xmi("type"): "uml:LiteralString",
                    _xmi("id"): f"{edge_id}_guard",
                    "value": edge.guard,
                },
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
