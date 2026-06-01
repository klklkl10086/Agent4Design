"""Shared data contracts for C analysis, XMI generation, and Rhapsody sync."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unexpected fields so integration mistakes fail early."""

    model_config = ConfigDict(extra="forbid")


class CTypeInfo(StrictModel):
    """Structured representation of a C declaration type."""

    base_type: str = Field(..., min_length=1, description="Base type without modifiers, for example T_UBYTE.")
    is_const: bool = Field(False, description="Whether the declaration contains const.")
    is_static: bool = Field(False, description="Whether the declaration contains static.")
    pointer_modifier: str = Field("", description="Pointer modifier such as *, **, or an empty string.")
    array_multiplicity: str = Field("", description="Array size such as 10 or 5][10.")
    raw_declaration: str = Field("", description="Original C declaration.")


class FunctionArgument(StrictModel):
    """A function argument and its C type."""

    name: str = Field(..., min_length=1, description="Argument name.")
    type_info: CTypeInfo


class FunctionSpec(StrictModel):
    """A C function signature."""

    name: str = Field(..., min_length=1, description="Function name.")
    arguments: List[FunctionArgument] = Field(default_factory=list, description="Ordered function arguments.")
    return_type_info: CTypeInfo


class MacroSpec(StrictModel):
    """A simple C macro definition."""

    name: str = Field(..., min_length=1, description="Macro name.")
    type_info: Optional[CTypeInfo] = Field(None, description="Optional inferred C type.")
    value: str = Field("", description="Macro value or expression.")
    raw_declaration: str = Field("", description="Original #define declaration.")


class VariableSpec(StrictModel):
    """A C variable definition."""

    name: str = Field(..., min_length=1, description="Variable name.")
    type_info: CTypeInfo
    initial_value: Optional[str] = Field(None, description="Optional initial value.")
    raw_declaration: str = Field("", description="Original C declaration.")


ActivityNodeType = Literal["Initial", "Action", "Decision", "Merge", "Final"]


class ActivityNode(StrictModel):
    """A node in an activity graph."""

    id: str = Field(..., min_length=1, description="Unique node id referenced by edge source and target.")
    type: ActivityNodeType = Field(..., description="Activity node type.")
    label: str = Field("", description="Short text displayed in the diagram.")
    description: str = Field("", description="Related code fragment or semantic description.")


class ActivityEdge(StrictModel):
    """A directed control-flow edge in an activity graph."""

    source: str = Field(..., min_length=1, description="Source node id.")
    target: str = Field(..., min_length=1, description="Target node id.")
    guard: str = Field("", description="Optional branch guard, for example true, false, or a switch case.")


class ActivityGraph(StrictModel):
    """A function-level activity graph."""

    nodes: List[ActivityNode] = Field(default_factory=list)
    edges: List[ActivityEdge] = Field(default_factory=list)


class ElementSummary(StrictModel):
    """Serializable summary of a Rhapsody model element."""

    name: str
    meta_class: str
    path: str = ""
    created: bool = False
