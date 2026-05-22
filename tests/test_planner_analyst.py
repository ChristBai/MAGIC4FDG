"""Unit tests for the rewritten planner and analyst agents."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.pipeline.state import (
    HarnessSlot, KnowledgeStore, PipelineState, StrategySelection,
)


def _make_knowledge(n_apis: int = 6) -> KnowledgeStore:
    apis = []
    for i in range(n_apis):
        cat = ["parse", "create", "delete", "modify", "query", "serialize"][i % 6]
        apis.append({
            "name": f"api_{cat}_{i}",
            "signature": f"int (Ctx *, const uint8_t *, size_t)",
            "return_type": "int",
            "params": [
                {"name": "ctx", "type": "Ctx *", "nullable": True, "ownership": "borrow"},
                {"name": "data", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"},
                {"name": "len", "type": "size_t", "nullable": False, "ownership": "borrow"},
            ],
            "category": cat,
            "preconditions": [],
            "group": "",
            "doc_comment": "",
        })
    return KnowledgeStore(
        api_entries=apis,
        call_graph={},
        type_definitions=[],
        macro_constants=[],
        slot_knowledge={},
    )


def _make_slot(slot_id: str = "slot_0", status: str = "active",
               plateau: int = 0, strategy: str = "parse-centric") -> HarnessSlot:
    return HarnessSlot(
        slot_id=slot_id,
        group_id="test_group",
        target_apis=["api_parse_0", "api_create_1"],
        current_source="",
        best_source="int LLVMFuzzerTestOneInput() {}",
        best_coverage=45.0,
        best_branch_coverage=30.0,
        best_uncovered_lines=[],
        best_function_coverage=[],
        strategy_history=[strategy],
        coverage_history=[45.0],
        status=status,
        plateau_count=plateau,
    )


class TestPlannerInitial(unittest.TestCase):
    """Test Round 1: API grouping → strategy matching → slot creation."""

    @patch("src.agents.planner._load_strategy_metadata")
    def test_plan_initial_creates_slots(self, mock_meta):
        from src.agents.planner import planner_node

        mock_meta.return_value = [{"id": "parse-centric", "name": "Parse", "best_for": ["parsers"]}]
        state = {
            "round": 0,
            "target_config": {"library_name": "testlib", "max_groups": 5},
            "knowledge": _make_knowledge(6),
            "harness_slots": [],
            "strategy_selections": [],
            "messages": [],
        }
        result = planner_node(state)
        self.assertIn("harness_slots", result)
        self.assertIn("strategy_selections", result)
        self.assertTrue(len(result["harness_slots"]) >= 1)
        self.assertEqual(len(result["harness_slots"]), len(result["strategy_selections"]))
        # Each slot has required fields
        for slot in result["harness_slots"]:
            self.assertIn("slot_id", slot)
            self.assertIn("target_apis", slot)
            self.assertIn("group_id", slot)
            self.assertEqual(slot["status"], "active")

    @patch("src.agents.planner._load_strategy_metadata")
    def test_plan_initial_assigns_strategies(self, mock_meta):
        from src.agents.planner import planner_node

        mock_meta.return_value = []
        state = {
            "round": 0,
            "target_config": {"library_name": "testlib"},
            "knowledge": _make_knowledge(6),
            "harness_slots": [],
            "strategy_selections": [],
            "messages": [],
        }
        result = planner_node(state)
        for sel in result["strategy_selections"]:
            self.assertIn("strategy_id", sel)
            self.assertIn("slot_id", sel)
            self.assertTrue(len(sel["target_apis"]) > 0)


class TestPlannerIterate(unittest.TestCase):
    """Test Round 2+: per-slot independent iteration."""

    def test_active_slots_get_selections(self):
        from src.agents.planner import planner_node

        slots = [_make_slot("slot_0"), _make_slot("slot_1")]
        state = {
            "round": 1,
            "target_config": {"library_name": "testlib"},
            "knowledge": _make_knowledge(),
            "harness_slots": slots,
            "strategy_selections": [],
            "slot_coverage_analyses": {},
            "messages": [],
        }
        result = planner_node(state)
        self.assertEqual(len(result["strategy_selections"]), 2)

    def test_converged_slots_skipped(self):
        from src.agents.planner import planner_node

        slots = [_make_slot("slot_0", status="converged"), _make_slot("slot_1")]
        state = {
            "round": 1,
            "target_config": {"library_name": "testlib"},
            "knowledge": _make_knowledge(),
            "harness_slots": slots,
            "strategy_selections": [],
            "slot_coverage_analyses": {},
            "messages": [],
        }
        result = planner_node(state)
        self.assertEqual(len(result["strategy_selections"]), 1)
        self.assertEqual(result["strategy_selections"][0]["slot_id"], "slot_1")

    def test_plateau_triggers_targeted_expansion(self):
        from src.agents.planner import planner_node

        slots = [_make_slot("slot_0", plateau=2)]
        state = {
            "round": 2,
            "target_config": {"library_name": "testlib"},
            "knowledge": _make_knowledge(),
            "harness_slots": slots,
            "strategy_selections": [],
            "slot_coverage_analyses": {},
            "messages": [],
        }
        result = planner_node(state)
        self.assertEqual(result["strategy_selections"][0]["strategy_id"], "targeted-expansion")

    def test_high_plateau_converges_slot(self):
        from src.agents.planner import planner_node

        slots = [_make_slot("slot_0", plateau=4)]
        state = {
            "round": 3,
            "target_config": {"library_name": "testlib"},
            "knowledge": _make_knowledge(),
            "harness_slots": slots,
            "strategy_selections": [],
            "slot_coverage_analyses": {},
            "messages": [],
        }
        result = planner_node(state)
        self.assertEqual(len(result["strategy_selections"]), 0)
        converged = [s for s in result["harness_slots"] if s["status"] == "converged"]
        self.assertEqual(len(converged), 1)

    def test_extract_targets_from_analyst(self):
        from src.agents.planner import _extract_slot_targets

        slot = _make_slot("slot_0")
        analysis = {
            "uncovered_clusters": [
                {"functions": ["new_func_a", "new_func_b"]},
            ]
        }
        targets = _extract_slot_targets(slot, analysis)
        self.assertIn("api_parse_0", targets)  # original
        self.assertIn("new_func_a", targets)   # from analyst


class TestAnalyst(unittest.TestCase):
    """Test analyst per-slot isolation."""

    def test_parse_analyst_response_valid_json(self):
        from src.agents.analyst import _parse_analyst_response

        raw = '{"constraints": [{"target": "func_a", "precondition": "needs init"}], "uncovered_clusters": []}'
        result = _parse_analyst_response(raw)
        self.assertEqual(len(result["constraints"]), 1)
        self.assertEqual(result["constraints"][0]["target"], "func_a")

    def test_parse_analyst_response_with_markdown(self):
        from src.agents.analyst import _parse_analyst_response

        raw = '```json\n{"constraints": [], "uncovered_clusters": [{"functions": ["f1"], "root_cause": "no init"}]}\n```'
        result = _parse_analyst_response(raw)
        self.assertEqual(len(result["uncovered_clusters"]), 1)

    def test_parse_analyst_response_invalid(self):
        from src.agents.analyst import _parse_analyst_response

        raw = "This is not JSON at all, just plain text analysis."
        result = _parse_analyst_response(raw)
        self.assertIn("constraints", result)
        self.assertEqual(len(result["constraints"]), 1)
        self.assertIn("general", result["constraints"][0]["target"])

    def test_update_slot_knowledge_merges(self):
        from src.agents.analyst import _update_slot_knowledge

        existing = {
            "constraints_discovered": [{"api": "old", "constraint": "x"}],
            "positive_patterns": [],
            "negative_patterns": [],
        }
        analysis = {
            "knowledge_updates": {
                "constraints_discovered": [{"api": "new", "constraint": "y"}],
                "positive_patterns": [{"pattern": "init before use", "coverage_gain": 5.0}],
                "negative_patterns": [],
            }
        }
        updated = _update_slot_knowledge(existing, analysis, round_num=2)
        self.assertEqual(len(updated["constraints_discovered"]), 2)
        self.assertEqual(updated["constraints_discovered"][1]["round"], 2)
        self.assertEqual(len(updated["positive_patterns"]), 1)

    def test_update_slot_knowledge_empty_analysis(self):
        from src.agents.analyst import _update_slot_knowledge

        existing = {"constraints_discovered": [], "positive_patterns": [], "negative_patterns": []}
        analysis = {}
        updated = _update_slot_knowledge(existing, analysis, round_num=1)
        self.assertEqual(updated, existing)

    @patch("src.agents.analyst.create_llm")
    @patch("src.agents.analyst.load_source_context")
    def test_analyst_node_per_slot(self, mock_source_ctx, mock_llm_factory):
        from src.agents.analyst import analyst_node

        mock_source_ctx.return_value = None
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"constraints": [{"target": "f", "precondition": "p"}], "uncovered_clusters": [], "knowledge_updates": {}}'
        mock_response.response_metadata = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        mock_llm.invoke.return_value = mock_response
        mock_llm_factory.return_value = mock_llm

        state = {
            "target_config": {"library_name": "test"},
            "knowledge": _make_knowledge(2),
            "harness_slots": [_make_slot("slot_0"), _make_slot("slot_1")],
            "messages": [],
            "round": 0,
        }
        result = analyst_node(state)
        self.assertIn("slot_coverage_analyses", result)
        self.assertIn("slot_0", result["slot_coverage_analyses"])
        self.assertIn("slot_1", result["slot_coverage_analyses"])
        # round incremented
        self.assertEqual(result["round"], 1)
        # slot_knowledge updated per slot
        self.assertIn("slot_0", result["knowledge"]["slot_knowledge"])
        self.assertIn("slot_1", result["knowledge"]["slot_knowledge"])


if __name__ == "__main__":
    unittest.main()
