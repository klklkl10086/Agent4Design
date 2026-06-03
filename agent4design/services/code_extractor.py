"""Extract conservative model specs from C source files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable, List, Sequence

from pydantic import Field

from agent4design.domain.models import (
    ActivityEdge,
    ActivityGraph,
    ActivityNode,
    CTypeInfo,
    FunctionArgument,
    FunctionSpec,
    MacroSpec,
    StrictModel,
    VariableSpec,
)


COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
FUNCTION_HEADER_RE = re.compile(
    r"(?P<return_type>[A-Za-z_][\w\s\*]*?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<params>[^;{}()]*)\)\s*\{",
    re.MULTILINE,
)
FUNCTION_PROTOTYPE_RE = re.compile(
    r"(?P<return_type>[A-Za-z_][\w\s\*]*?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<params>[^;{}()]*)\)\s*;",
    re.MULTILINE,
)
MACRO_RE = re.compile(r"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)\b(?P<value>.*)$")

CONTROL_NAMES = {"if", "for", "while", "switch", "return", "sizeof"}
TYPE_QUALIFIERS = {
    "const",
    "extern",
    "inline",
    "register",
    "static",
    "volatile",
    "__inline",
    "__inline__",
}
SKIP_DECLARATION_PREFIXES = (
    "#",
    "case ",
    "default:",
    "do ",
    "else",
    "enum ",
    "for ",
    "goto ",
    "if ",
    "return ",
    "struct ",
    "switch ",
    "typedef ",
    "union ",
    "while ",
)


class SourceFileSummary(StrictModel):
    """One source file considered by the code path extractor."""

    path: str
    bytes_read: int = 0
    parsed: bool = False
    error: str = ""


class ExtractedActivitySpec(StrictModel):
    """Activity graph extracted for one function."""

    function_spec: FunctionSpec
    graph: ActivityGraph


class CodePathExtractionRequest(StrictModel):
    """A file or directory containing C source code to model."""

    path: str = Field(..., min_length=1)
    recursive: bool = True
    include_headers: bool = True
    include_activities: bool = True
    max_file_bytes: int = Field(1_000_000, ge=1)
    max_activity_statements: int = Field(30, ge=1)


class CodePathExtractionResult(StrictModel):
    """Structured model specs extracted from a code path."""

    success: bool
    source_files: List[SourceFileSummary] = Field(default_factory=list)
    macros: List[MacroSpec] = Field(default_factory=list)
    variables: List[VariableSpec] = Field(default_factory=list)
    functions: List[FunctionSpec] = Field(default_factory=list)
    activities: List[ExtractedActivitySpec] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class FunctionParseResult(StrictModel):
    """Internal parse result for one function body or prototype."""

    spec: FunctionSpec
    body: str = ""
    start: int = 0
    end: int = 0


def _strip_comments(source: str) -> str:
    return COMMENT_RE.sub("", source)


def _source_files(request: CodePathExtractionRequest) -> List[Path]:
    root = Path(request.path).resolve()
    extensions = {".c", ".cc", ".cpp", ".cxx"}
    if request.include_headers:
        extensions.update({".h", ".hpp", ".hh"})

    if root.is_file():
        return [root] if root.suffix.lower() in extensions else []
    if not root.is_dir():
        raise FileNotFoundError(f"Code path not found: {root}")

    iterator: Iterable[Path]
    iterator = root.rglob("*") if request.recursive else root.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in extensions
    )


def _find_matching_brace(source: str, open_index: int) -> int:
    depth = 0
    index = open_index
    while index < len(source):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _array_multiplicity(array_text: str) -> str:
    sizes = re.findall(r"\[([^\]]*)\]", array_text or "")
    return "][".join(size.strip() for size in sizes)


def _type_info_from_text(type_text: str, raw_declaration: str = "") -> CTypeInfo:
    pointer_modifier = "*" * type_text.count("*")
    no_stars = type_text.replace("*", " ")
    tokens = _normalize_spaces(no_stars).split()
    is_const = "const" in tokens
    is_static = "static" in tokens
    base_tokens = [token for token in tokens if token not in TYPE_QUALIFIERS]
    base_type = " ".join(base_tokens) or "void"
    return CTypeInfo(
        base_type=base_type,
        is_const=is_const,
        is_static=is_static,
        pointer_modifier=pointer_modifier,
        raw_declaration=raw_declaration,
    )


def _parse_declaration_type_and_name(
    declaration: str,
    *,
    fallback_name: str = "unnamed",
) -> tuple[str, CTypeInfo]:
    clean = declaration.strip().strip(";")
    clean = re.split(r"\s*=\s*", clean, maxsplit=1)[0].strip()
    match = re.match(
        r"(?P<type>.*?)\s*(?P<pointer>\*+)?\s*"
        r"(?P<name>[A-Za-z_]\w*)\s*(?P<array>(?:\[[^\]]*\])*)$",
        clean,
    )
    if not match:
        return fallback_name, _type_info_from_text(clean, declaration)

    type_text = f"{match.group('type')} {match.group('pointer') or ''}"
    type_info = _type_info_from_text(type_text, declaration)
    array_multiplicity = _array_multiplicity(match.group("array") or "")
    if array_multiplicity:
        type_info = type_info.model_copy(
            update={"array_multiplicity": array_multiplicity}
        )
    return match.group("name"), type_info


def _parse_parameters(params: str) -> List[FunctionArgument]:
    clean_params = params.strip()
    if not clean_params or clean_params == "void":
        return []

    arguments = []
    for index, raw_param in enumerate(clean_params.split(","), start=1):
        param = raw_param.strip()
        if not param or param == "void":
            continue
        name, type_info = _parse_declaration_type_and_name(
            param,
            fallback_name=f"arg{index}",
        )
        arguments.append(FunctionArgument(name=name, type_info=type_info))
    return arguments


def _function_spec(return_type: str, name: str, params: str) -> FunctionSpec:
    return FunctionSpec(
        name=name,
        arguments=_parse_parameters(params),
        return_type_info=_type_info_from_text(return_type, return_type),
    )


def _extract_functions(source: str) -> List[FunctionParseResult]:
    functions: List[FunctionParseResult] = []
    for match in FUNCTION_HEADER_RE.finditer(source):
        name = match.group("name")
        if name in CONTROL_NAMES:
            continue
        open_brace = source.find("{", match.end() - 1)
        close_brace = _find_matching_brace(source, open_brace)
        if close_brace < 0:
            continue
        functions.append(
            FunctionParseResult(
                spec=_function_spec(
                    match.group("return_type"),
                    name,
                    match.group("params"),
                ),
                body=source[open_brace + 1 : close_brace],
                start=match.start(),
                end=close_brace + 1,
            )
        )
    return functions


def _extract_prototypes(source_without_functions: str) -> List[FunctionSpec]:
    specs = []
    for match in FUNCTION_PROTOTYPE_RE.finditer(source_without_functions):
        name = match.group("name")
        if name in CONTROL_NAMES:
            continue
        specs.append(
            _function_spec(
                match.group("return_type"),
                name,
                match.group("params"),
            )
        )
    return specs


def _blank_spans(source: str, spans: Sequence[tuple[int, int]]) -> str:
    chars = list(source)
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = "\n" if chars[index] == "\n" else " "
    return "".join(chars)


def _blank_preprocessor_lines(source: str) -> str:
    lines = []
    in_continuation = False
    for line in source.splitlines():
        is_directive = in_continuation or line.lstrip().startswith("#")
        in_continuation = is_directive and line.rstrip().endswith("\\")
        lines.append("" if is_directive else line)
    return "\n".join(lines)


def _infer_macro_type(value: str) -> CTypeInfo | None:
    clean = value.strip()
    if re.fullmatch(r"[-+]?(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*", clean):
        return CTypeInfo(base_type="int", raw_declaration=clean)
    if re.fullmatch(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?[fFlL]?", clean):
        return CTypeInfo(base_type="float", raw_declaration=clean)
    if re.fullmatch(r"'.*'", clean):
        return CTypeInfo(base_type="char", raw_declaration=clean)
    if re.fullmatch(r'".*"', clean):
        return CTypeInfo(
            base_type="char",
            pointer_modifier="*",
            raw_declaration=clean,
        )
    return None


def _extract_macros(source: str, warnings: List[str]) -> List[MacroSpec]:
    macros = []
    continued = ""
    for line in source.splitlines():
        current = continued + line.strip()
        if current.endswith("\\"):
            continued = current[:-1] + " "
            continue
        continued = ""

        match = MACRO_RE.match(current)
        if not match:
            continue
        name = match.group("name")
        value = match.group("value").strip()
        if value.startswith("("):
            warnings.append(f"Skipped function-like macro: {name}")
            continue
        macros.append(
            MacroSpec(
                name=name,
                type_info=_infer_macro_type(value),
                value=value,
                raw_declaration=current,
            )
        )
    return macros


def _top_level_declarations(source: str) -> Iterable[str]:
    buffer = []
    depth = 0
    for char in source:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if depth == 0:
            buffer.append(char)
            if char == ";":
                yield "".join(buffer)
                buffer = []
        elif char == "\n":
            buffer.append("\n")


def _extract_variables(source_without_functions: str, warnings: List[str]) -> List[VariableSpec]:
    variables = []
    for declaration in _top_level_declarations(source_without_functions):
        clean = _normalize_spaces(declaration)
        if not clean:
            continue
        lowered = clean.lower()
        if lowered.startswith(SKIP_DECLARATION_PREFIXES):
            continue
        if "(" in clean or ")" in clean:
            continue
        if "," in clean:
            warnings.append(f"Skipped multi-declarator global declaration: {clean}")
            continue

        name, type_info = _parse_declaration_type_and_name(clean)
        if not name or type_info.base_type in {"", "void"}:
            continue
        initial_value = None
        if "=" in clean:
            initial_value = clean.split("=", 1)[1].strip().rstrip(";")
        variables.append(
            VariableSpec(
                name=name,
                type_info=type_info,
                initial_value=initial_value,
                raw_declaration=clean,
            )
        )
    return variables


def _statement_summaries(body: str, limit: int) -> tuple[List[str], bool]:
    clean = re.sub(r"[{}]", "\n", body)
    parts = []
    for line in clean.splitlines():
        for part in line.split(";"):
            statement = _normalize_spaces(part)
            if statement:
                parts.append(statement)
    return parts[:limit], len(parts) > limit


def _activity_for_function(
    function: FunctionParseResult,
    *,
    max_statements: int,
) -> tuple[ExtractedActivitySpec, bool]:
    statements, truncated = _statement_summaries(function.body, max_statements)
    nodes = [
        ActivityNode(id="start", type="Initial", label="start"),
    ]
    edges: List[ActivityEdge] = []
    previous = "start"

    if not statements:
        nodes.append(
            ActivityNode(
                id="body",
                type="Action",
                label="body",
                description="No executable statements were extracted.",
            )
        )
        edges.append(ActivityEdge(source=previous, target="body"))
        previous = "body"
    else:
        for index, statement in enumerate(statements, start=1):
            node_id = f"action_{index}"
            nodes.append(
                ActivityNode(
                    id=node_id,
                    type="Action",
                    label=statement[:60],
                    description=statement,
                )
            )
            edges.append(ActivityEdge(source=previous, target=node_id))
            previous = node_id

    nodes.append(ActivityNode(id="end", type="Final", label="end"))
    edges.append(ActivityEdge(source=previous, target="end"))
    return (
        ExtractedActivitySpec(
            function_spec=function.spec,
            graph=ActivityGraph(nodes=nodes, edges=edges),
        ),
        truncated,
    )


def extract_code_path_model(
    request: CodePathExtractionRequest,
) -> CodePathExtractionResult:
    """Extract model specs from a C source file or directory."""
    source_files = []
    macros_by_name: dict[str, MacroSpec] = {}
    variables_by_name: dict[str, VariableSpec] = {}
    functions_by_name: dict[str, FunctionSpec] = {}
    activities_by_name: dict[str, ExtractedActivitySpec] = {}
    warnings: List[str] = []
    errors: List[str] = []

    try:
        paths = _source_files(request)
    except Exception as exc:
        return CodePathExtractionResult(
            success=False,
            errors=[str(exc)],
        )

    if not paths:
        return CodePathExtractionResult(
            success=False,
            errors=[f"No C source files found under: {Path(request.path).resolve()}"],
        )

    for path in paths:
        summary = SourceFileSummary(path=str(path))
        try:
            if path.stat().st_size > request.max_file_bytes:
                summary.error = (
                    f"File exceeds max_file_bytes={request.max_file_bytes}."
                )
                source_files.append(summary)
                warnings.append(f"Skipped oversized source file: {path}")
                continue

            source = path.read_text(encoding="utf-8", errors="replace")
            summary.bytes_read = len(source.encode("utf-8", errors="replace"))
            stripped = _strip_comments(source)
            parsed_functions = _extract_functions(stripped)
            spans = [(function.start, function.end) for function in parsed_functions]
            without_functions = _blank_spans(stripped, spans)
            declarations_source = _blank_preprocessor_lines(without_functions)

            for macro in _extract_macros(stripped, warnings):
                macros_by_name.setdefault(macro.name, macro)
            for variable in _extract_variables(declarations_source, warnings):
                variables_by_name.setdefault(variable.name, variable)
            for function in parsed_functions:
                functions_by_name.setdefault(function.spec.name, function.spec)
                if request.include_activities:
                    activity, truncated = _activity_for_function(
                        function,
                        max_statements=request.max_activity_statements,
                    )
                    activities_by_name.setdefault(function.spec.name, activity)
                    if truncated:
                        warnings.append(
                            f"Activity for {function.spec.name} was truncated to "
                            f"{request.max_activity_statements} statements."
                        )
            for prototype in _extract_prototypes(declarations_source):
                functions_by_name.setdefault(prototype.name, prototype)
            summary.parsed = True
        except Exception as exc:
            summary.error = str(exc)
            errors.append(f"{path}: {exc}")
        source_files.append(summary)

    return CodePathExtractionResult(
        success=not errors,
        source_files=source_files,
        macros=list(macros_by_name.values()),
        variables=list(variables_by_name.values()),
        functions=list(functions_by_name.values()),
        activities=list(activities_by_name.values()),
        warnings=warnings,
        errors=errors,
    )
