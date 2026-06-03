"""Segment C source with a parser and extract model specs through an LLM hook."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, List, Literal, Protocol

from pydantic import Field

from agent4design.domain.models import (
    ActivityGraph,
    FunctionSpec,
    MacroSpec,
    StrictModel,
    VariableSpec,
)


CodeLanguage = Literal["c", "cpp", "header", "unknown"]
CodeSegmentKind = Literal[
    "function_definition",
    "declaration",
    "type_definition",
    "preprocessor_include",
    "preprocessor_define",
    "preprocessor_conditional",
    "preprocessor",
    "unknown",
]

TREE_SITTER_NODE_KINDS: dict[str, CodeSegmentKind] = {
    "function_definition": "function_definition",
    "declaration": "declaration",
    "type_definition": "type_definition",
    "preproc_include": "preprocessor_include",
    "preproc_def": "preprocessor_define",
    "preproc_function_def": "preprocessor_define",
    "preproc_if": "preprocessor_conditional",
    "preproc_ifdef": "preprocessor_conditional",
    "preproc_ifndef": "preprocessor_conditional",
    "preproc_elif": "preprocessor_conditional",
    "preproc_else": "preprocessor_conditional",
    "preproc_call": "preprocessor",
}
CONTEXT_KINDS = {
    "preprocessor_include",
    "preprocessor_define",
    "type_definition",
}


class SourceFileSummary(StrictModel):
    """One source file considered by the code path extractor."""

    path: str
    bytes_read: int = 0
    parsed: bool = False
    segment_count: int = 0
    error: str = ""


class CodeContextSnippet(StrictModel):
    """A small earlier syntax segment supplied as context to an LLM."""

    kind: CodeSegmentKind
    symbol: str = ""
    start_line: int
    end_line: int
    source: str


class CodeSyntaxSegment(StrictModel):
    """One parser-identified source chunk with original code intact."""

    id: str
    path: str
    language: CodeLanguage = "unknown"
    kind: CodeSegmentKind
    symbol: str = ""
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    source: str
    context: List[CodeContextSnippet] = Field(default_factory=list)


class ExtractedActivitySpec(StrictModel):
    """Activity graph extracted for one function."""

    function_spec: FunctionSpec
    graph: ActivityGraph


class CodeSegmentExtraction(StrictModel):
    """Model specs extracted from one syntax segment by an LLM."""

    segment_id: str
    macros: List[MacroSpec] = Field(default_factory=list)
    variables: List[VariableSpec] = Field(default_factory=list)
    functions: List[FunctionSpec] = Field(default_factory=list)
    activities: List[ExtractedActivitySpec] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class CodePathExtractionRequest(StrictModel):
    """A file or directory containing C source code to model."""

    path: str = Field(..., min_length=1)
    recursive: bool = True
    include_headers: bool = True
    include_activities: bool = True
    include_segments: bool = True
    require_model_extraction: bool = True
    max_file_bytes: int = Field(1_000_000, ge=1)
    max_segment_chars: int = Field(12_000, ge=1)
    max_context_segments: int = Field(12, ge=0)
    segmenter: Literal["auto", "tree_sitter"] = "auto"


class CodePathExtractionResult(StrictModel):
    """Structured model specs extracted from a code path."""

    success: bool
    source_files: List[SourceFileSummary] = Field(default_factory=list)
    segments: List[CodeSyntaxSegment] = Field(default_factory=list)
    segment_results: List[CodeSegmentExtraction] = Field(default_factory=list)
    macros: List[MacroSpec] = Field(default_factory=list)
    variables: List[VariableSpec] = Field(default_factory=list)
    functions: List[FunctionSpec] = Field(default_factory=list)
    activities: List[ExtractedActivitySpec] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class CodeSegmentModelExtractor(Protocol):
    """A semantic extractor that can call an LLM for one parser segment."""

    def extract(
        self,
        segment: CodeSyntaxSegment,
        *,
        include_activities: bool,
    ) -> CodeSegmentExtraction:
        """Return strictly validated model specs for one syntax segment."""


class CodeSegmenter(Protocol):
    """A parser-backed source segmenter."""

    name: str

    def segment_file(
        self,
        path: Path,
        source: str,
        request: CodePathExtractionRequest,
    ) -> List[CodeSyntaxSegment]:
        """Return parser-identified syntax segments for one source file."""


@dataclass(frozen=True)
class _TreeSitterRuntime:
    parser: Any


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


def _language_for_path(path: Path) -> CodeLanguage:
    suffix = path.suffix.lower()
    if suffix == ".c":
        return "c"
    if suffix in {".cc", ".cpp", ".cxx"}:
        return "cpp"
    if suffix in {".h", ".hpp", ".hh"}:
        return "header"
    return "unknown"


def _line_slice(source: str, start_line: int, end_line: int) -> str:
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])


def _stable_segment_id(path: Path, kind: CodeSegmentKind, start_byte: int, source: str) -> str:
    digest = hashlib.sha1(
        f"{path}:{kind}:{start_byte}:{source[:80]}".encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"{path.name}:{start_byte}:{digest}"


def _node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def _iter_descendants(node: Any) -> Iterable[Any]:
    stack = list(getattr(node, "children", []) or [])
    while stack:
        current = stack.pop(0)
        yield current
        stack[0:0] = list(getattr(current, "children", []) or [])


def _first_named_child_text(source_bytes: bytes, node: Any, types: set[str]) -> str:
    for child in _iter_descendants(node):
        if getattr(child, "type", "") in types:
            return _node_text(source_bytes, child)
    return ""


def _symbol_for_node(source_bytes: bytes, node: Any, kind: CodeSegmentKind) -> str:
    if kind == "function_definition":
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            return _first_named_child_text(source_bytes, declarator, {"identifier"})
    if kind in {"declaration", "type_definition"}:
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            symbol = _first_named_child_text(
                source_bytes,
                declarator,
                {"identifier", "type_identifier", "field_identifier"},
            )
            if symbol:
                return symbol
    if kind.startswith("preprocessor"):
        name = node.child_by_field_name("name")
        if name is not None:
            return _node_text(source_bytes, name)
    return _first_named_child_text(
        source_bytes,
        node,
        {"identifier", "type_identifier", "field_identifier"},
    )


def _tree_sitter_runtime() -> _TreeSitterRuntime:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_c
    except ImportError as exc:
        raise RuntimeError(
            "Code segmentation requires the parser extra. Install it with "
            "`pip install -e .[parser]` so tree-sitter can split C code by "
            "syntax nodes instead of regex matching."
        ) from exc

    raw_language = tree_sitter_c.language()
    try:
        language = Language(raw_language)
    except TypeError:
        language = raw_language

    try:
        parser = Parser(language)
    except TypeError:
        parser = Parser()
        try:
            parser.language = language
        except AttributeError:
            parser.set_language(language)
    return _TreeSitterRuntime(parser=parser)


class TreeSitterCodeSegmenter:
    """Use tree-sitter's C grammar to split source into top-level syntax chunks."""

    name = "tree_sitter"

    def __init__(self) -> None:
        self.runtime = _tree_sitter_runtime()

    def segment_file(
        self,
        path: Path,
        source: str,
        request: CodePathExtractionRequest,
    ) -> List[CodeSyntaxSegment]:
        source_bytes = source.encode("utf-8", errors="replace")
        tree = self.runtime.parser.parse(source_bytes)
        segments: List[CodeSyntaxSegment] = []
        for child in getattr(tree.root_node, "children", []) or []:
            if not getattr(child, "is_named", True):
                continue
            kind = TREE_SITTER_NODE_KINDS.get(child.type, "unknown")
            if kind == "unknown" and child.type.startswith("preproc_"):
                kind = "preprocessor"
            if kind == "unknown":
                continue

            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1
            source_text = _node_text(source_bytes, child)
            if not source_text.strip():
                continue
            segments.append(
                CodeSyntaxSegment(
                    id=_stable_segment_id(path, kind, child.start_byte, source_text),
                    path=str(path),
                    language=_language_for_path(path),
                    kind=kind,
                    symbol=_symbol_for_node(source_bytes, child, kind),
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    source=_line_slice(source, start_line, end_line),
                )
            )

        return _attach_context(segments, request.max_context_segments)


def _attach_context(
    segments: List[CodeSyntaxSegment],
    max_context_segments: int,
) -> List[CodeSyntaxSegment]:
    by_file: dict[str, List[CodeSyntaxSegment]] = {}
    for segment in segments:
        by_file.setdefault(segment.path, []).append(segment)

    enriched: List[CodeSyntaxSegment] = []
    for path_segments in by_file.values():
        context: List[CodeContextSnippet] = []
        for segment in sorted(path_segments, key=lambda item: item.start_byte):
            enriched.append(
                segment.model_copy(
                    update={"context": context[-max_context_segments:]}
                )
            )
            if segment.kind in CONTEXT_KINDS:
                context.append(
                    CodeContextSnippet(
                        kind=segment.kind,
                        symbol=segment.symbol,
                        start_line=segment.start_line,
                        end_line=segment.end_line,
                        source=segment.source,
                    )
                )
    return sorted(enriched, key=lambda item: (item.path, item.start_byte))


def _resolve_segmenter(request: CodePathExtractionRequest) -> CodeSegmenter:
    if request.segmenter in {"auto", "tree_sitter"}:
        return TreeSitterCodeSegmenter()
    raise ValueError(f"Unsupported code segmenter: {request.segmenter}")


def _deduplicate_by_name(items: Iterable[Any]) -> list[Any]:
    by_name = {}
    unnamed = []
    for item in items:
        name = getattr(item, "name", "")
        if name:
            by_name.setdefault(name, item)
        else:
            unnamed.append(item)
    return [*by_name.values(), *unnamed]


def _merge_segment_results(
    results: List[CodeSegmentExtraction],
) -> tuple[
    List[MacroSpec],
    List[VariableSpec],
    List[FunctionSpec],
    List[ExtractedActivitySpec],
    List[str],
    List[str],
]:
    warnings: List[str] = []
    errors: List[str] = []
    macros: List[MacroSpec] = []
    variables: List[VariableSpec] = []
    functions: List[FunctionSpec] = []
    activities: List[ExtractedActivitySpec] = []

    for result in results:
        warnings.extend(
            f"{result.segment_id}: {warning}" for warning in result.warnings
        )
        errors.extend(f"{result.segment_id}: {error}" for error in result.errors)
        macros.extend(result.macros)
        variables.extend(result.variables)
        functions.extend(result.functions)
        activities.extend(result.activities)

    activities_by_name: dict[str, ExtractedActivitySpec] = {}
    unnamed_activities: List[ExtractedActivitySpec] = []
    for activity in activities:
        name = activity.function_spec.name
        if name:
            activities_by_name.setdefault(name, activity)
        else:
            unnamed_activities.append(activity)

    return (
        _deduplicate_by_name(macros),
        _deduplicate_by_name(variables),
        _deduplicate_by_name(functions),
        [*activities_by_name.values(), *unnamed_activities],
        warnings,
        errors,
    )


def segment_code_path(
    request: CodePathExtractionRequest,
    *,
    segmenter: CodeSegmenter | None = None,
) -> tuple[List[SourceFileSummary], List[CodeSyntaxSegment], List[str], List[str]]:
    """Read source files and split them into parser-identified syntax segments."""
    source_files: List[SourceFileSummary] = []
    segments: List[CodeSyntaxSegment] = []
    warnings: List[str] = []
    errors: List[str] = []

    try:
        paths = _source_files(request)
    except Exception as exc:
        return [], [], [], [str(exc)]

    if not paths:
        return (
            [],
            [],
            [],
            [f"No C source files found under: {Path(request.path).resolve()}"],
        )

    try:
        resolved_segmenter = segmenter or _resolve_segmenter(request)
    except Exception as exc:
        return (
            [SourceFileSummary(path=str(path)) for path in paths],
            [],
            [],
            [str(exc)],
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
            file_segments = resolved_segmenter.segment_file(path, source, request)
            for segment in file_segments:
                if len(segment.source) > request.max_segment_chars:
                    warnings.append(
                        f"{segment.id}: parser segment has {len(segment.source)} "
                        f"characters, exceeding max_segment_chars="
                        f"{request.max_segment_chars}. Keeping the syntax node "
                        "intact for auditability."
                    )
            summary.segment_count = len(file_segments)
            summary.parsed = True
            segments.extend(file_segments)
        except Exception as exc:
            summary.error = str(exc)
            errors.append(f"{path}: {exc}")
        source_files.append(summary)

    return source_files, segments, warnings, errors


def extract_code_path_model(
    request: CodePathExtractionRequest,
    *,
    model_extractor: CodeSegmentModelExtractor | None = None,
    segmenter: CodeSegmenter | None = None,
) -> CodePathExtractionResult:
    """Segment C code with a parser, then let an LLM extractor produce specs."""
    source_files, segments, warnings, errors = segment_code_path(
        request,
        segmenter=segmenter,
    )
    if errors:
        return CodePathExtractionResult(
            success=False,
            source_files=source_files,
            segments=segments if request.include_segments else [],
            warnings=warnings,
            errors=errors,
        )

    if model_extractor is None:
        message = (
            "LLM code model extractor is not configured. The tool returned "
            "parser syntax segments only; configure the LLM adapter or pass a "
            "CodeSegmentModelExtractor to extract Rhapsody model specs."
        )
        if request.require_model_extraction:
            errors.append(message)
        else:
            warnings.append(message)
        return CodePathExtractionResult(
            success=not errors,
            source_files=source_files,
            segments=segments if request.include_segments else [],
            warnings=warnings,
            errors=errors,
        )

    segment_results: List[CodeSegmentExtraction] = []
    for segment in segments:
        try:
            result = model_extractor.extract(
                segment,
                include_activities=request.include_activities,
            )
            if result.segment_id != segment.id:
                result = result.model_copy(update={"segment_id": segment.id})
            segment_results.append(result)
        except Exception as exc:
            segment_results.append(
                CodeSegmentExtraction(
                    segment_id=segment.id,
                    errors=[str(exc)],
                )
            )

    (
        macros,
        variables,
        functions,
        activities,
        result_warnings,
        result_errors,
    ) = _merge_segment_results(segment_results)
    warnings.extend(result_warnings)
    errors.extend(result_errors)

    return CodePathExtractionResult(
        success=not errors,
        source_files=source_files,
        segments=segments if request.include_segments else [],
        segment_results=segment_results,
        macros=macros,
        variables=variables,
        functions=functions,
        activities=activities,
        warnings=warnings,
        errors=errors,
    )


def parse_segment_extraction_json(
    content: str,
    *,
    segment_id: str,
) -> CodeSegmentExtraction:
    """Validate one LLM JSON response for a syntax segment."""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM code extraction response must be a JSON object.")
    payload.setdefault("segment_id", segment_id)
    return CodeSegmentExtraction.model_validate(payload)
