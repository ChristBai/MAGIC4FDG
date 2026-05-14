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


class TestResearchAgent(unittest.TestCase):
    def test_read_source_files(self) -> None:
        from src.agents.research import _read_source_files

        config = {
            "header": "examples/cjson_lib/cJSON.h",
            "source_files": ["examples/cjson_lib/cJSON.c"],
        }
        source = _read_source_files(config)
        self.assertIn("cJSON", source)
        self.assertGreater(len(source), 100)

    def test_render_research_prompt(self) -> None:
        from src.agents.research import _render_research_prompt

        config = {
            "library_name": "cjson",
            "language": "C",
            "description": "Parse JSON string",
        }
        prompt = _render_research_prompt(config, "void foo() {}")
        self.assertNotIn("{{", prompt)
        self.assertIn("cjson", prompt)
        self.assertIn("void foo()", prompt)

    @patch("src.agents.research.create_llm")
    def test_research_node_calls_llm(self, mock_create_llm: MagicMock) -> None:
        from src.agents.research import research_node

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Analysis: this library parses JSON"
        mock_llm.invoke.return_value = mock_response
        mock_create_llm.return_value = mock_llm

        state = {
            "target_config": {
                "library_name": "cjson",
                "language": "C",
                "description": "Parse JSON",
                "header": "examples/cjson_lib/cJSON.h",
                "source_files": ["examples/cjson_lib/cJSON.c"],
                "include_dirs": ["examples/cjson_lib"],
            },
            "messages": [],
        }

        result = research_node(state)
        self.assertEqual(result["research_summary"], "Analysis: this library parses JSON")
        self.assertIn("[Research]", result["messages"][0])
        mock_llm.invoke.assert_called_once()


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


class TestGenerationAgent(unittest.TestCase):
    def test_build_prompt_parse(self) -> None:
        from src.agents.generation import _build_prompt

        config = {
            "library_name": "cjson",
            "header": "cJSON.h",
            "language": "C",
            "include_dirs": ["."],
        }
        prompt = _build_prompt(config, "parse", "some research")
        self.assertIn("cjson", prompt)
        self.assertIn("some research", prompt)
        self.assertIn("Parse-Centric", prompt)
        self.assertNotIn("{{", prompt)

    def test_build_prompt_api_chain(self) -> None:
        from src.agents.generation import _build_prompt

        config = {
            "library_name": "cjson",
            "header": "cJSON.h",
            "language": "C",
            "include_dirs": ["."],
        }
        prompt = _build_prompt(config, "api-chain", "Found 5 branches in parser")
        self.assertIn("Found 5 branches", prompt)
        self.assertIn("API-Chain", prompt)

    def test_build_prompt_roundtrip(self) -> None:
        from src.agents.generation import _build_prompt

        config = {
            "library_name": "cjson",
            "header": "cJSON.h",
            "language": "C",
            "include_dirs": ["."],
        }
        prompt = _build_prompt(config, "roundtrip", "research data")
        self.assertIn("research data", prompt)
        self.assertIn("Round-Trip", prompt)

    def test_make_variant_id(self) -> None:
        from src.agents.generation import _make_variant_id

        config = {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.4}
        vid = _make_variant_id(config)
        self.assertIn("basic", vid)
        self.assertIn("t04", vid)

    @patch("src.agents.generation._build_variant_configs")
    @patch("src.agents.generation.create_variant_llm")
    def test_generation_node_produces_variants(self, mock_create: MagicMock, mock_configs: MagicMock) -> None:
        from src.agents.generation import generation_node

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```cpp\nint main() {}\n```"
        mock_llm.invoke.return_value = mock_response
        mock_create.return_value = mock_llm

        mock_configs.return_value = [
            {"model": "gpt-4o", "prompt_strategy": "parse", "temperature": 0.7},
            {"model": "gpt-4o", "prompt_strategy": "api-chain", "temperature": 0.7},
        ]

        state = {
            "target_config": {
                "library_name": "test",
                "header": "test.h",
                "language": "C",
                "include_dirs": ["."],
                "source_files": ["test.c"],
            },
            "research_summary": "analysis here",
            "messages": [],
            "temperature_schedule": [0.7],
            "current_temp_idx": 0,
        }

        result = generation_node(state)
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["variants"][0]["compile_status"], "pending")
        self.assertIn("int main()", result["variants"][0]["source_code"])


class TestGraph(unittest.TestCase):
    def test_graph_compiles(self) -> None:
        from src.pipeline.graph import compile_graph

        graph = compile_graph()
        self.assertIsNotNone(graph)


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
        self.assertEqual(len(uncovered), 1)
        self.assertEqual(uncovered[0]["line_no"], 5)

    @patch("src.agents.coverage.run_fuzz_with_coverage")
    @patch("src.agents.coverage._analyze_reachability")
    def test_coverage_node(self, mock_reachability: MagicMock, mock_fuzz: MagicMock) -> None:
        from src.agents.coverage import coverage_node

        mock_reachability.return_value = {10, 20, 30}
        mock_fuzz.return_value = {
            "coverage": {
                "totals": {
                    "lines": {"count": 100, "covered": 65},
                    "branches": {"count": 20, "covered": 12},
                },
                "files": [],
            }
        }

        state = {
            "target_config": {
                "target_name": "test",
                "source_files": ["test.c"],
                "include_dirs": ["."],
            },
            "variants": [
                {
                    "id": "test_v1",
                    "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                    "source_code": "int main() {}",
                    "compile_status": "ok",
                    "compile_errors": "",
                    "patch_attempts": 0,
                    "coverage_pct": 0.0,
                    "branch_coverage_pct": 0.0,
                    "uncovered_lines": [],
                    "unique_coverage": [],
                    "iteration": 0,
                },
            ],
            "fuzz_seconds": 10,
            "messages": [],
            "iteration": 0,
            "target_coverage": 70.0,
            "max_iterations": 3,
            "current_temp_idx": 0,
        }

        result = coverage_node(state)
        self.assertEqual(result["iteration"], 1)
        self.assertAlmostEqual(result["best_coverage"], 65.0)
        self.assertEqual(result["variants"][0]["coverage_pct"], 65.0)
        self.assertAlmostEqual(result["variants"][0]["branch_coverage_pct"], 60.0)
        self.assertIn("[Coverage]", result["messages"][0])


class TestRefinementAgent(unittest.TestCase):
    def test_build_variant_analysis(self) -> None:
        from src.agents.refinement import _build_variant_analysis

        variants = [
            {
                "id": "v1",
                "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                "source_code": "int main() {}",
                "compile_status": "ok",
                "compile_errors": "",
                "patch_attempts": 0,
                "coverage_pct": 55.0,
                "branch_coverage_pct": 40.0,
                "uncovered_lines": [],
                "unique_coverage": [10, 20],
            },
        ]
        analysis = _build_variant_analysis(variants)
        self.assertIn("v1", analysis)
        self.assertIn("55.0%", analysis)

    def test_build_uncovered_reachable(self) -> None:
        from src.agents.refinement import _build_uncovered_reachable

        variants = [
            {
                "id": "v1",
                "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                "source_code": "",
                "compile_status": "ok",
                "compile_errors": "",
                "patch_attempts": 0,
                "coverage_pct": 50.0,
                "branch_coverage_pct": 30.0,
                "uncovered_lines": [
                    {"file": "test.c", "line_no": 15, "reachable": True},
                    {"file": "test.c", "line_no": 25, "reachable": False},
                ],
                "unique_coverage": [],
            },
        ]
        result = _build_uncovered_reachable(variants)
        self.assertIn("test.c:15", result)
        self.assertNotIn("test.c:25", result)

    def test_render_refinement_prompt(self) -> None:
        from src.agents.refinement import _render_refinement_prompt

        config = {
            "library_name": "cjson",
            "header": "cJSON.h",
            "language": "C",
        }
        variants = [
            {
                "id": "v1",
                "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                "source_code": "int main() {}",
                "compile_status": "ok",
                "compile_errors": "",
                "patch_attempts": 0,
                "coverage_pct": 50.0,
                "branch_coverage_pct": 30.0,
                "uncovered_lines": [],
                "unique_coverage": [],
            },
        ]
        prompt = _render_refinement_prompt(config, variants)
        self.assertIn("cjson", prompt)
        self.assertNotIn("{{", prompt)

    @patch("src.agents.refinement.create_llm")
    def test_refinement_node_produces_fused(self, mock_create_llm: MagicMock) -> None:
        from src.agents.refinement import refinement_node

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```cpp\n#include <cstdint>\nint LLVMFuzzerTestOneInput() {}\n```"
        mock_llm.invoke.return_value = mock_response
        mock_create_llm.return_value = mock_llm

        state = {
            "target_config": {
                "library_name": "test",
                "header": "test.h",
                "language": "C",
            },
            "variants": [
                {
                    "id": "v1",
                    "config": {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                    "source_code": "int main() {}",
                    "compile_status": "ok",
                    "compile_errors": "",
                    "patch_attempts": 0,
                    "coverage_pct": 50.0,
                    "branch_coverage_pct": 30.0,
                    "uncovered_lines": [],
                    "unique_coverage": [],
                    "iteration": 0,
                },
            ],
            "messages": [],
            "iteration": 1,
            "current_temp_idx": 0,
        }

        result = refinement_node(state)
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["variants"][-1]["id"], "fused_iter0")
        self.assertEqual(result["variants"][-1]["compile_status"], "pending")
        self.assertIn("[Refinement]", result["messages"][0])


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
            "iteration": 2,
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
            "messages": ["[Research] Done", "[Coverage] v1: 65.0%"],
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
