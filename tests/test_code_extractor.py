"""Offline tests for parser-segmented code extraction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent4design.domain.models import (
    ActivityEdge,
    ActivityGraph,
    ActivityNode,
    CTypeInfo,
    FunctionArgument,
    FunctionSpec,
    MacroSpec,
    VariableSpec,
)
from agent4design.services.code_extractor import (
    CodePathExtractionRequest,
    CodeSegmentExtraction,
    CodeSyntaxSegment,
    extract_code_path_model,
    segment_code_path,
)


class FakeSegmenter:
    name = "fake"

    def segment_file(self, path: Path, source: str, request: CodePathExtractionRequest):
        return [
            CodeSyntaxSegment(
                id="demo.c:0:macro",
                path=str(path),
                language="c",
                kind="preprocessor_define",
                symbol="LIMIT",
                start_line=1,
                end_line=1,
                start_byte=0,
                end_byte=16,
                source="#define LIMIT 10",
            ),
            CodeSyntaxSegment(
                id="demo.c:32:function",
                path=str(path),
                language="c",
                kind="function_definition",
                symbol="add",
                start_line=3,
                end_line=6,
                start_byte=32,
                end_byte=len(source.encode("utf-8")),
                source="\n".join(source.splitlines()[2:]),
            ),
        ]


class FakeModelExtractor:
    def extract(self, segment: CodeSyntaxSegment, *, include_activities: bool):
        if segment.kind == "preprocessor_define":
            return CodeSegmentExtraction(
                segment_id=segment.id,
                macros=[
                    MacroSpec(
                        name="LIMIT",
                        type_info=CTypeInfo(base_type="int"),
                        value="10",
                        raw_declaration=segment.source,
                    )
                ],
            )

        function = FunctionSpec(
            name="add",
            arguments=[
                FunctionArgument(
                    name="a",
                    type_info=CTypeInfo(base_type="int"),
                ),
                FunctionArgument(
                    name="b",
                    type_info=CTypeInfo(base_type="int"),
                ),
            ],
            return_type_info=CTypeInfo(base_type="int"),
        )
        activities = []
        if include_activities:
            activities.append(
                {
                    "function_spec": function.model_dump(mode="json"),
                    "graph": ActivityGraph(
                        nodes=[
                            ActivityNode(id="start", type="Initial"),
                            ActivityNode(id="body", type="Action", label="return"),
                            ActivityNode(id="end", type="Final"),
                        ],
                        edges=[
                            ActivityEdge(source="start", target="body"),
                            ActivityEdge(source="body", target="end"),
                        ],
                    ).model_dump(mode="json"),
                }
            )
        return CodeSegmentExtraction(
            segment_id=segment.id,
            variables=[
                VariableSpec(
                    name="counter",
                    type_info=CTypeInfo(base_type="int", is_static=True),
                    initial_value="1",
                    raw_declaration="static int counter = 1;",
                )
            ],
            functions=[function],
            activities=activities,
        )


class CodeExtractorTests(unittest.TestCase):
    def test_segments_then_merges_llm_model_specs(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "demo.c"
            source_path.write_text(
                "\n".join(
                    [
                        "#define LIMIT 10",
                        "static int counter = 1;",
                        "int add(int a, int b) {",
                        "    return a + b;",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            result = extract_code_path_model(
                CodePathExtractionRequest(path=str(source_path)),
                segmenter=FakeSegmenter(),
                model_extractor=FakeModelExtractor(),
            )

        self.assertTrue(result.success)
        self.assertEqual([segment.kind for segment in result.segments], [
            "preprocessor_define",
            "function_definition",
        ])
        self.assertEqual([macro.name for macro in result.macros], ["LIMIT"])
        self.assertEqual([variable.name for variable in result.variables], ["counter"])
        self.assertEqual([function.name for function in result.functions], ["add"])
        self.assertEqual(len(result.activities), 1)

    def test_can_return_segments_without_model_extraction(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "demo.c"
            source_path.write_text("#define LIMIT 10\n", encoding="utf-8")

            result = extract_code_path_model(
                CodePathExtractionRequest(
                    path=str(source_path),
                    require_model_extraction=False,
                ),
                segmenter=FakeSegmenter(),
            )

        self.assertTrue(result.success)
        self.assertEqual(len(result.segments), 2)
        self.assertIn("LLM code model extractor is not configured", result.warnings[0])

    def test_reports_empty_code_directory(self) -> None:
        with TemporaryDirectory() as directory:
            result = extract_code_path_model(
                CodePathExtractionRequest(path=directory),
                segmenter=FakeSegmenter(),
                model_extractor=FakeModelExtractor(),
            )

        self.assertFalse(result.success)
        self.assertIn("No C source files found", result.errors[0])

    def test_default_segmenter_requires_parser_extra_when_missing(self) -> None:
        if importlib.util.find_spec("tree_sitter") is not None:
            self.skipTest("tree-sitter is installed in this environment")

        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "demo.c"
            source_path.write_text("#define LIMIT 10\n", encoding="utf-8")

            source_files, segments, warnings, errors = segment_code_path(
                CodePathExtractionRequest(path=str(source_path))
            )

        self.assertEqual(len(source_files), 1)
        self.assertEqual(segments, [])
        self.assertEqual(warnings, [])
        self.assertIn("parser extra", errors[0])


if __name__ == "__main__":
    unittest.main()
