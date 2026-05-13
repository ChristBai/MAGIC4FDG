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


class TestGraph(unittest.TestCase):
    def test_graph_compiles(self) -> None:
        from agents.graph import compile_graph

        graph = compile_graph()
        self.assertIsNotNone(graph)


if __name__ == "__main__":
    unittest.main()
