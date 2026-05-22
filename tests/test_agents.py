"""Unit tests for the agents package."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestState(unittest.TestCase):
    def test_variant_config_structure(self) -> None:
        from src.pipeline.state import VariantConfig

        config: VariantConfig = {
            "model": "gpt-4o",
            "prompt_strategy": "basic",
            "temperature": 0.7,
        }
        self.assertEqual(config["model"], "gpt-4o")

    def test_driver_variant_structure(self) -> None:
        from src.pipeline.state import DriverVariant

        variant: DriverVariant = {
            "id": "test_variant",
            "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
            "source_code": "int main() {}",
            "compile_status": "pending",
            "compile_errors": "",
            "patch_attempts": 0,
            "coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "uncovered_lines": [],
            "unique_coverage": [],
        }
        self.assertEqual(variant["compile_status"], "pending")


class TestPatchingAgent(unittest.TestCase):
    def test_render_patch_prompt(self) -> None:
        from src.agents.patching import _render_patch_prompt

        config = {
            "library_name": "cjson",
            "header": "cJSON.h",
            "include_dirs": ["examples/cjson_lib"],
        }
        prompt = _render_patch_prompt(config, "bad code", "error: unknown type")
        self.assertIn("cjson", prompt)
        self.assertIn("bad code", prompt)
        self.assertIn("error: unknown type", prompt)
        self.assertNotIn("{{", prompt)

    @patch("src.agents.patching.compile_driver")
    def test_patch_variant_compiles_first_try(self, mock_compile: MagicMock) -> None:
        from src.agents.patching import _patch_single_variant

        mock_compile.return_value = (True, "")

        variant = {
            "id": "test_v",
            "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
            "source_code": "int main() {}",
            "compile_status": "pending",
            "compile_errors": "",
            "patch_attempts": 0,
            "coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "uncovered_lines": [],
            "unique_coverage": [],
        }
        messages: list[str] = []
        result = _patch_single_variant(variant, {}, 3, messages)
        self.assertEqual(result["compile_status"], "ok")
        self.assertIn("first try", messages[0])

    @patch("src.agents.patching.create_llm")
    @patch("src.agents.patching.compile_driver")
    def test_patch_variant_fixes_after_retry(self, mock_compile: MagicMock, mock_create_llm: MagicMock) -> None:
        from src.agents.patching import _patch_single_variant

        mock_compile.side_effect = [
            (False, "error: missing header"),
            (True, ""),
        ]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```cpp\n#include <cstdint>\nint main() {}\n```"
        mock_llm.invoke.return_value = mock_response
        mock_create_llm.return_value = mock_llm

        variant = {
            "id": "test_v",
            "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
            "source_code": "int main() {}",
            "compile_status": "pending",
            "compile_errors": "",
            "patch_attempts": 0,
            "coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "uncovered_lines": [],
            "unique_coverage": [],
        }
        messages: list[str] = []
        result = _patch_single_variant(variant, {"library_name": "test", "header": "h.h", "include_dirs": []}, 3, messages)
        self.assertEqual(result["compile_status"], "ok")
        self.assertEqual(result["patch_attempts"], 1)


class TestCoverageAgent(unittest.TestCase):
    def test_parse_cfg_output(self) -> None:
        from src.agents.coverage import _parse_cfg_output

        cfg_text = """entry:
  br label %loop
loop:
  br i1 %cond, label %body, label %exit
body:
  br label %loop
exit:
  ret void
"""
        graph = _parse_cfg_output(cfg_text)
        self.assertIn("entry", graph)
        self.assertIn("loop", graph["entry"])
        self.assertIn("body", graph.get("loop", set()))
        self.assertIn("exit", graph.get("loop", set()))

    def test_reachable_blocks(self) -> None:
        from src.agents.coverage import _reachable_blocks

        graph = {
            "entry": {"a", "b"},
            "a": {"c"},
            "b": set(),
            "c": set(),
            "unreachable": {"d"},
            "d": set(),
        }
        reachable = _reachable_blocks(graph, "entry")
        self.assertIn("entry", reachable)
        self.assertIn("a", reachable)
        self.assertIn("b", reachable)
        self.assertIn("c", reachable)
        self.assertNotIn("unreachable", reachable)
        self.assertNotIn("d", reachable)

    def test_mark_reachability(self) -> None:
        from src.agents.coverage import _mark_reachability

        uncovered = [
            {"file": "test.c", "line_no": 10, "count": 0, "reachable": True},
            {"file": "test.c", "line_no": 20, "count": 0, "reachable": True},
            {"file": "test.c", "line_no": 30, "count": 0, "reachable": True},
        ]
        reachable_lines = {10, 30, 40, 50}
        result = _mark_reachability(uncovered, reachable_lines)
        self.assertTrue(result[0]["reachable"])
        self.assertFalse(result[1]["reachable"])
        self.assertTrue(result[2]["reachable"])

    def test_extract_uncovered_lines(self) -> None:
        from src.agents.coverage import _extract_uncovered_lines

        report = {
            "coverage": {
                "files": [
                    {
                        "filename": "test.c",
                        "summary": {"lines": {"count": 10, "covered": 7}},
                        "segments": [
                            [5, 1, 0, True, True],
                            [10, 1, 3, True, True],
                        ],
                    }
                ]
            }
        }
        uncovered = _extract_uncovered_lines(report)
        # Segment [5,1,0,True,True] expands to lines 5-9 (until next segment at line 10)
        self.assertEqual(len(uncovered), 5)
        self.assertEqual(uncovered[0]["line_no"], 5)
        self.assertEqual(uncovered[-1]["line_no"], 9)


class TestGraph(unittest.TestCase):
    def test_graph_compiles(self) -> None:
        from src.pipeline.graph import compile_graph

        graph = compile_graph()
        self.assertIsNotNone(graph)


class TestSupervisor(unittest.TestCase):
    def test_supervisor_imports(self) -> None:
        from src.pipeline.supervisor import run_pipeline, main
        self.assertTrue(callable(run_pipeline))
        self.assertTrue(callable(main))


class TestReport(unittest.TestCase):
    def test_generate_report(self) -> None:
        from src.pipeline.report import generate_report

        state = {
            "target_config": {
                "library_name": "cjson",
                "description": "Lightweight JSON parser",
            },
            "round": 2,
            "target_coverage": 70.0,
            "best_coverage": 65.0,
            "fuzz_seconds": 15,
            "variants": [
                {
                    "id": "v1",
                    "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                    "source_code": "int main() {}",
                    "compile_status": "ok",
                    "compile_errors": "",
                    "patch_attempts": 1,
                    "coverage_pct": 65.0,
                    "branch_coverage_pct": 45.0,
                    "uncovered_lines": [
                        {"file": "cJSON.c", "line_no": 100, "reachable": True},
                        {"file": "cJSON.c", "line_no": 200, "reachable": False},
                    ],
                    "unique_coverage": [],
                },
                {
                    "id": "v2",
                    "config": {"model": "deepseek-chat", "prompt_strategy": "research", "temperature": 0.4},
                    "source_code": "",
                    "compile_status": "failed",
                    "compile_errors": "error",
                    "patch_attempts": 3,
                    "coverage_pct": 0.0,
                    "branch_coverage_pct": 0.0,
                    "uncovered_lines": [],
                    "unique_coverage": [],
                },
            ],
            "messages": ["[Knowledge] Done", "[Coverage] v1: 65.0%"],
        }

        report = generate_report(state)
        self.assertIn("cjson", report)
        self.assertIn("65.0%", report)
        self.assertIn("NOT reached", report)
        self.assertIn("v1", report)
        self.assertIn("Execution Log", report)

    def test_variant_table_empty(self) -> None:
        from src.pipeline.report import _variant_table

        result = _variant_table([])
        self.assertIn("No variants generated.", result)


if __name__ == "__main__":
    unittest.main()
