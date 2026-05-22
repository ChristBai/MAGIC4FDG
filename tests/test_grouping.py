"""Unit tests for src/knowledge/grouping.py — API grouping and strategy matching."""

from __future__ import annotations

import unittest

from src.knowledge.grouping import (
    group_apis,
    match_strategy,
    _is_fuzzable,
    _extract_features,
    _cluster_by_context_type,
    _merge_small_groups,
    _split_large_groups,
    _has_multiple_variants,
)
from src.pipeline.state import APIGroup, KnowledgeStore


def _make_api(name: str, params: list[dict] | None = None, category: str = "utility",
              return_type: str = "void", doc_comment: str = "") -> dict:
    if params is None:
        params = [{"name": "data", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"}]
    return {
        "name": name,
        "signature": f"{return_type} ({', '.join(p['type'] for p in params)})",
        "return_type": return_type,
        "params": params,
        "category": category,
        "preconditions": [],
        "group": "",
        "doc_comment": doc_comment,
    }


def _make_knowledge(apis: list[dict], call_graph: dict | None = None) -> KnowledgeStore:
    return KnowledgeStore(
        api_entries=apis,
        call_graph=call_graph or {},
        type_definitions=[],
        macro_constants=[],
        slot_knowledge={},
    )


class TestIsFuzzable(unittest.TestCase):
    def test_internal_prefix_excluded(self):
        api = _make_api("_internal_func")
        self.assertFalse(_is_fuzzable(api))

    def test_double_underscore_excluded(self):
        api = _make_api("__hidden")
        self.assertFalse(_is_fuzzable(api))

    def test_deprecated_excluded(self):
        api = _make_api("old_func", doc_comment="This function is deprecated.")
        self.assertFalse(_is_fuzzable(api))

    def test_no_params_excluded(self):
        api = _make_api("get_version", params=[])
        self.assertFalse(_is_fuzzable(api))

    def test_single_non_pointer_excluded(self):
        api = _make_api("set_flag", params=[{"name": "flag", "type": "int", "nullable": False, "ownership": "borrow"}])
        self.assertFalse(_is_fuzzable(api))

    def test_single_pointer_included(self):
        api = _make_api("parse", params=[{"name": "buf", "type": "const char *", "nullable": True, "ownership": "borrow"}])
        self.assertTrue(_is_fuzzable(api))

    def test_multi_param_included(self):
        api = _make_api("process", params=[
            {"name": "ctx", "type": "void *", "nullable": True, "ownership": "borrow"},
            {"name": "len", "type": "size_t", "nullable": False, "ownership": "borrow"},
        ])
        self.assertTrue(_is_fuzzable(api))


class TestClustering(unittest.TestCase):
    def test_cluster_by_context_type(self):
        apis = [
            _make_api("ctx_init", params=[{"name": "ctx", "type": "MyCtx *", "nullable": True, "ownership": "borrow"}]),
            _make_api("ctx_run", params=[{"name": "ctx", "type": "MyCtx *", "nullable": True, "ownership": "borrow"}]),
            _make_api("parse_buf", params=[{"name": "buf", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"}]),
        ]
        groups = _cluster_by_context_type(apis)
        self.assertIn("MyCtx *", groups)
        self.assertEqual(len(groups["MyCtx *"]), 2)

    def test_merge_small_groups(self):
        groups = {"A *": [_make_api("a1")], "B *": [_make_api("b1"), _make_api("b2"), _make_api("b3")]}
        merged = _merge_small_groups(groups, min_size=2)
        self.assertIn("B *", merged)
        self.assertIn("_misc", merged)
        self.assertEqual(len(merged["_misc"]), 1)

    def test_split_large_groups(self):
        apis = [_make_api(f"func_{i}", category="parse" if i < 6 else "create") for i in range(12)]
        groups = {"BigCtx *": apis}
        split = _split_large_groups(groups, max_size=10)
        self.assertNotIn("BigCtx *", split)
        self.assertTrue(len(split) >= 2)


class TestFeatureExtraction(unittest.TestCase):
    def test_has_parser(self):
        apis = [_make_api("parse_json", category="parse")]
        features = _extract_features(apis, {})
        self.assertIn("has_parser", features)

    def test_has_lifecycle(self):
        apis = [_make_api("obj_create", category="create"), _make_api("obj_delete", category="delete")]
        features = _extract_features(apis, {})
        self.assertIn("has_lifecycle", features)

    def test_has_lifecycle_delete_modify(self):
        apis = [_make_api("obj_set", category="modify"), _make_api("obj_free", category="delete")]
        features = _extract_features(apis, {})
        self.assertIn("has_lifecycle", features)

    def test_has_buffer_params(self):
        apis = [_make_api("read_buf", params=[
            {"name": "buf", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"},
            {"name": "len", "type": "size_t", "nullable": False, "ownership": "borrow"},
        ])]
        features = _extract_features(apis, {})
        self.assertIn("has_buffer_params", features)

    def test_has_callbacks(self):
        apis = [_make_api("register_cb", params=[
            {"name": "cb", "type": "void (*)(int)", "nullable": True, "ownership": "borrow"},
        ])]
        features = _extract_features(apis, {})
        self.assertIn("has_callbacks", features)

    def test_has_state_context(self):
        apis = [_make_api("ctx_op", params=[
            {"name": "ctx", "type": "MyCtx *", "nullable": True, "ownership": "borrow"},
        ])]
        features = _extract_features(apis, {})
        self.assertIn("has_state_context", features)

    def test_has_serializer(self):
        apis = [_make_api("to_string", category="serialize")]
        features = _extract_features(apis, {})
        self.assertIn("has_serializer", features)

    def test_has_multiple_variants(self):
        apis = [_make_api(f"encode_{suffix}") for suffix in ("utf8", "ascii", "latin1")]
        self.assertTrue(_has_multiple_variants(apis))

    def test_no_multiple_variants(self):
        apis = [_make_api("parse"), _make_api("encode")]
        self.assertFalse(_has_multiple_variants(apis))


class TestStrategyMatching(unittest.TestCase):
    def test_parse_centric_match(self):
        group = APIGroup(group_id="parsers", apis=["parse_json"], context_type="x",
                         features=["has_parser", "has_buffer_params"])
        result = match_strategy(group)
        self.assertIn(result, ("parse-centric", "roundtrip", "structure-aware"))

    def test_roundtrip_beats_parse_when_serializer(self):
        group = APIGroup(group_id="codec", apis=["parse", "serialize"], context_type="x",
                         features=["has_parser", "has_serializer", "has_buffer_params"])
        result = match_strategy(group)
        self.assertEqual(result, "roundtrip")

    def test_stateful_match(self):
        group = APIGroup(group_id="ctx", apis=["ctx_new", "ctx_free"], context_type="Ctx *",
                         features=["has_state_context", "has_lifecycle"])
        result = match_strategy(group)
        self.assertIn(result, ("stateful", "multi-api-sequence"))

    def test_callback_match(self):
        group = APIGroup(group_id="events", apis=["register"], context_type="x",
                         features=["has_callbacks"])
        result = match_strategy(group)
        self.assertEqual(result, "callback-driven")

    def test_fallback_to_multi_api(self):
        group = APIGroup(group_id="misc", apis=["foo"], context_type="x", features=[])
        result = match_strategy(group)
        self.assertEqual(result, "multi-api-sequence")

    def test_absolute_match_count_priority(self):
        """策略匹配优先看绝对匹配数，而非比例。"""
        group = APIGroup(group_id="complex", apis=["a", "b"], context_type="x",
                         features=["has_parser", "has_buffer_params", "has_state_context", "has_lifecycle"])
        result = match_strategy(group)
        # multi-api-sequence matches 2 features (lifecycle + state_context)
        # parse-centric matches 2 features (parser + buffer)
        # stateful matches 2 features (state_context + lifecycle)
        # Should pick one with best priority among tied strategies
        self.assertIn(result, ("parse-centric", "stateful", "multi-api-sequence"))


class TestGroupApis(unittest.TestCase):
    def test_basic_grouping(self):
        apis = [
            _make_api("parse_a", params=[{"name": "d", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"}], category="parse"),
            _make_api("parse_b", params=[{"name": "d", "type": "const uint8_t *", "nullable": True, "ownership": "borrow"}], category="parse"),
            _make_api("ctx_init", params=[{"name": "c", "type": "Ctx *", "nullable": True, "ownership": "borrow"}], category="create"),
            _make_api("ctx_free", params=[{"name": "c", "type": "Ctx *", "nullable": True, "ownership": "borrow"}], category="delete"),
        ]
        knowledge = _make_knowledge(apis)
        groups = group_apis(knowledge)
        self.assertTrue(len(groups) >= 1)
        all_apis_in_groups = set()
        for g in groups:
            all_apis_in_groups.update(g["apis"])
        # All fuzzable APIs should appear in some group
        for api in apis:
            self.assertIn(api["name"], all_apis_in_groups)

    def test_max_groups_limit(self):
        apis = [_make_api(f"func_{i}", params=[
            {"name": "ctx", "type": f"Type{i} *", "nullable": True, "ownership": "borrow"},
            {"name": "x", "type": "int", "nullable": False, "ownership": "borrow"},
        ]) for i in range(20)]
        knowledge = _make_knowledge(apis)
        groups = group_apis(knowledge, max_groups=3)
        self.assertLessEqual(len(groups), 3)

    def test_empty_knowledge(self):
        knowledge = _make_knowledge([])
        groups = group_apis(knowledge)
        self.assertEqual(groups, [])


if __name__ == "__main__":
    unittest.main()
