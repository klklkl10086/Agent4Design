"""Offline tests for conservative C code path extraction."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent4design.services.code_extractor import (
    CodePathExtractionRequest,
    extract_code_path_model,
)


class CodeExtractorTests(unittest.TestCase):
    def test_extracts_semantic_specs_and_activity_graph(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "demo.c"
            source_path.write_text(
                "\n".join(
                    [
                        "#define LIMIT 10",
                        "static int counter = 1;",
                        "int add(int a, int b) {",
                        "    int total = a + b;",
                        "    return total;",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            result = extract_code_path_model(
                CodePathExtractionRequest(path=str(source_path))
            )

        self.assertTrue(result.success)
        self.assertEqual([macro.name for macro in result.macros], ["LIMIT"])
        self.assertEqual(result.macros[0].type_info.base_type, "int")

        self.assertEqual([variable.name for variable in result.variables], ["counter"])
        self.assertTrue(result.variables[0].type_info.is_static)
        self.assertEqual(result.variables[0].initial_value, "1")

        self.assertEqual([function.name for function in result.functions], ["add"])
        self.assertEqual(
            [argument.name for argument in result.functions[0].arguments],
            ["a", "b"],
        )

        self.assertEqual(len(result.activities), 1)
        self.assertEqual(result.activities[0].function_spec.name, "add")
        self.assertGreaterEqual(len(result.activities[0].graph.nodes), 3)

    def test_reports_empty_code_directory(self) -> None:
        with TemporaryDirectory() as directory:
            result = extract_code_path_model(
                CodePathExtractionRequest(path=directory)
            )

        self.assertFalse(result.success)
        self.assertIn("No C source files found", result.errors[0])


if __name__ == "__main__":
    unittest.main()
