"""Unit tests for the agents package."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestState(unittest.TestCase):
    def test_variant_config_structure(self) -> None:
        from agents.state import VariantConfig

        config: VariantConfig = {
            "model": "gpt-4o",
            "prompt_strategy": "basic",
            "temperature": 0.7,
        }
        self.assertEqual(config["model"], "gpt-4o")

    def test_driver_variant_structure(self) -> None:
        from agents.state import DriverVariant

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
        from agents.research import _read_source_files

        config = {
            "header": "examples/cjson_lib/cJSON.h",
            "source_files": ["examples/cjson_lib/cJSON.c"],
        }
        source = _read_source_files(config)
        self.assertIn("cJSON", source)
        self.assertGreater(len(source), 100)

    def test_render_research_prompt(self) -> None:
        from agents.research import _render_research_prompt

        config = {
            "target_name": "cjson",
            "function_name": "cJSON_Parse",
            "signature": "cJSON *cJSON_Parse(const char *value)",
            "description": "Parse JSON string",
        }
        prompt = _render_research_prompt(config, "void foo() {}")
        self.assertNotIn("{{", prompt)
        self.assertIn("cJSON_Parse", prompt)
        self.assertIn("void foo()", prompt)

    @patch("agents.research.create_llm")
    def test_research_node_calls_llm(self, mock_create_llm: MagicMock) -> None:
        from agents.research import research_node

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Analysis: this function parses JSON"
        mock_llm.invoke.return_value = mock_response
        mock_create_llm.return_value = mock_llm

        state = {
            "target_config": {
                "target_name": "cjson",
                "function_name": "cJSON_Parse",
                "signature": "cJSON *cJSON_Parse(const char *value)",
                "description": "Parse JSON",
                "header": "examples/cjson_lib/cJSON.h",
                "source_files": ["examples/cjson_lib/cJSON.c"],
            },
            "messages": [],
        }

        result = research_node(state)
        self.assertEqual(result["research_summary"], "Analysis: this function parses JSON")
        self.assertIn("[Research]", result["messages"][0])
        mock_llm.invoke.assert_called_once()


class TestPatchingAgent(unittest.TestCase):
    def test_render_patch_prompt(self) -> None:
        from agents.patching import _render_patch_prompt

        config = {
            "function_name": "cJSON_Parse",
            "signature": "cJSON *cJSON_Parse(const char *value)",
            "header": "cJSON.h",
            "include_dirs": ["examples/cjson_lib"],
        }
        prompt = _render_patch_prompt(config, "bad code", "error: unknown type")
        self.assertIn("cJSON_Parse", prompt)
        self.assertIn("bad code", prompt)
        self.assertIn("error: unknown type", prompt)
        self.assertNotIn("{{", prompt)

    @patch("agents.patching.compile_driver")
    def test_patch_variant_compiles_first_try(self, mock_compile: MagicMock) -> None:
        from agents.patching import _patch_single_variant

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

    @patch("agents.patching.create_llm")
    @patch("agents.patching.compile_driver")
    def test_patch_variant_fixes_after_retry(self, mock_compile: MagicMock, mock_create_llm: MagicMock) -> None:
        from agents.patching import _patch_single_variant

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
        result = _patch_single_variant(variant, {"function_name": "f", "signature": "void f()", "header": "h.h", "include_dirs": []}, 3, messages)
        self.assertEqual(result["compile_status"], "ok")
        self.assertEqual(result["patch_attempts"], 1)


class TestGenerationAgent(unittest.TestCase):
    def test_build_prompt_basic(self) -> None:
        from agents.generation import _build_prompt

        config = {
            "target_name": "cjson",
            "function_name": "cJSON_Parse",
            "signature": "cJSON *cJSON_Parse(const char *value)",
            "header": "cJSON.h",
            "description": "Parse JSON",
            "language": "C",
            "cleanup_function": "cJSON_Delete",
            "source_files": ["cJSON.c"],
            "include_dirs": ["."],
            "seed_corpus": "seed/",
        }
        prompt = _build_prompt(config, "basic", "some research")
        self.assertIn("cJSON_Parse", prompt)
        self.assertNotIn("Additional Context", prompt)

    def test_build_prompt_research(self) -> None:
        from agents.generation import _build_prompt

        config = {
            "target_name": "cjson",
            "function_name": "cJSON_Parse",
            "signature": "cJSON *cJSON_Parse(const char *value)",
            "header": "cJSON.h",
            "description": "Parse JSON",
            "language": "C",
            "cleanup_function": "cJSON_Delete",
            "source_files": ["cJSON.c"],
            "include_dirs": ["."],
            "seed_corpus": "seed/",
        }
        prompt = _build_prompt(config, "research", "Found 5 branches in parser")
        self.assertIn("Additional Context", prompt)
        self.assertIn("Found 5 branches", prompt)

    def test_make_variant_id(self) -> None:
        from agents.generation import _make_variant_id

        config = {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.4}
        vid = _make_variant_id(config)
        self.assertIn("basic", vid)
        self.assertIn("t04", vid)

    @patch("agents.generation.create_variant_llm")
    def test_generation_node_produces_variants(self, mock_create: MagicMock) -> None:
        from agents.generation import generation_node

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```cpp\nint main() {}\n```"
        mock_llm.invoke.return_value = mock_response
        mock_create.return_value = mock_llm

        state = {
            "target_config": {
                "target_name": "test",
                "function_name": "test_func",
                "signature": "void test_func(const char *s)",
                "header": "test.h",
                "description": "test",
                "language": "C",
                "cleanup_function": "",
                "source_files": ["test.c"],
                "include_dirs": ["."],
                "seed_corpus": "seed/",
            },
            "research_summary": "analysis here",
            "variant_matrix": [
                {"model": "gpt-4o", "prompt_strategy": "basic", "temperature": 0.7},
                {"model": "gpt-4o", "prompt_strategy": "research", "temperature": 0.7},
            ],
            "messages": [],
        }

        result = generation_node(state)
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["variants"][0]["compile_status"], "pending")
        self.assertIn("int main()", result["variants"][0]["source_code"])


class TestGraph(unittest.TestCase):
    def test_graph_compiles(self) -> None:
        from agents.graph import compile_graph

        graph = compile_graph()
        self.assertIsNotNone(graph)


if __name__ == "__main__":
    unittest.main()
