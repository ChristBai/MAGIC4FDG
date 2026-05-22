"""Unit tests for coverage aggregation and graph routing logic."""

from __future__ import annotations

import unittest

from src.agents.coverage import _aggregate_project_coverage
from src.pipeline.graph import _route_after_analyst, _route_after_patching
from langgraph.graph import END


class TestAggregateProjectCoverage(unittest.TestCase):
    def test_empty_variants(self):
        cov, covered = _aggregate_project_coverage([])
        self.assertEqual(cov, 0.0)
        self.assertEqual(covered, set())

    def test_single_variant(self):
        variants = [{
            "compile_status": "ok",
            "covered_lines": [
                {"file": "a.c", "line_no": 1},
                {"file": "a.c", "line_no": 2},
                {"file": "a.c", "line_no": 3},
            ],
            "uncovered_lines": [
                {"file": "a.c", "line_no": 4},
                {"file": "a.c", "line_no": 5},
            ],
        }]
        cov, covered = _aggregate_project_coverage(variants)
        self.assertAlmostEqual(cov, 60.0)  # 3/5 = 60%
        self.assertEqual(len(covered), 3)

    def test_union_across_variants(self):
        variants = [
            {
                "compile_status": "ok",
                "covered_lines": [{"file": "a.c", "line_no": 1}, {"file": "a.c", "line_no": 2}],
                "uncovered_lines": [{"file": "a.c", "line_no": 3}, {"file": "a.c", "line_no": 4}],
            },
            {
                "compile_status": "ok",
                "covered_lines": [{"file": "a.c", "line_no": 3}, {"file": "a.c", "line_no": 4}],
                "uncovered_lines": [{"file": "a.c", "line_no": 1}, {"file": "a.c", "line_no": 2}],
            },
        ]
        cov, covered = _aggregate_project_coverage(variants)
        # Union: lines 1,2,3,4 all covered; total lines = 4
        self.assertAlmostEqual(cov, 100.0)

    def test_failed_variants_excluded(self):
        variants = [
            {
                "compile_status": "failed",
                "covered_lines": [{"file": "a.c", "line_no": 1}],
                "uncovered_lines": [],
            },
            {
                "compile_status": "ok",
                "covered_lines": [{"file": "a.c", "line_no": 2}],
                "uncovered_lines": [{"file": "a.c", "line_no": 3}],
            },
        ]
        cov, covered = _aggregate_project_coverage(variants)
        self.assertAlmostEqual(cov, 50.0)  # 1/2
        self.assertNotIn(("a.c", 1), covered)

    def test_overlapping_files(self):
        variants = [
            {
                "compile_status": "ok",
                "covered_lines": [{"file": "a.c", "line_no": 1}],
                "uncovered_lines": [{"file": "b.c", "line_no": 1}],
            },
            {
                "compile_status": "ok",
                "covered_lines": [{"file": "b.c", "line_no": 1}],
                "uncovered_lines": [{"file": "a.c", "line_no": 1}],
            },
        ]
        cov, covered = _aggregate_project_coverage(variants)
        # Both lines covered in union
        self.assertAlmostEqual(cov, 100.0)


class TestRouteAfterPatching(unittest.TestCase):
    def test_any_compiled_goes_to_coverage(self):
        state = {"variants": [
            {"compile_status": "failed"},
            {"compile_status": "ok"},
        ]}
        self.assertEqual(_route_after_patching(state), "coverage")

    def test_all_failed_goes_to_checkpoint(self):
        state = {"variants": [
            {"compile_status": "failed"},
            {"compile_status": "failed"},
        ]}
        self.assertEqual(_route_after_patching(state), "checkpoint")

    def test_empty_variants_goes_to_checkpoint(self):
        state = {"variants": []}
        self.assertEqual(_route_after_patching(state), "checkpoint")


class TestRouteAfterAnalyst(unittest.TestCase):
    def test_target_reached_ends(self):
        state = {
            "best_coverage": 80.0,
            "target_coverage": 70.0,
            "round": 1,
            "max_rounds": 10,
            "harness_slots": [{"status": "active"}],
            "coverage_plateau_count": 0,
        }
        self.assertEqual(_route_after_analyst(state), END)

    def test_max_rounds_ends(self):
        state = {
            "best_coverage": 50.0,
            "target_coverage": 100.0,
            "round": 10,
            "max_rounds": 10,
            "harness_slots": [{"status": "active"}],
            "coverage_plateau_count": 0,
        }
        self.assertEqual(_route_after_analyst(state), END)

    def test_all_converged_ends(self):
        state = {
            "best_coverage": 50.0,
            "target_coverage": 100.0,
            "round": 2,
            "max_rounds": 10,
            "harness_slots": [{"status": "converged"}, {"status": "converged"}],
            "coverage_plateau_count": 0,
        }
        self.assertEqual(_route_after_analyst(state), END)

    def test_plateau_ends(self):
        state = {
            "best_coverage": 50.0,
            "target_coverage": 100.0,
            "round": 5,
            "max_rounds": 10,
            "harness_slots": [{"status": "active"}],
            "coverage_plateau_count": 3,
        }
        self.assertEqual(_route_after_analyst(state), END)

    def test_continues_when_active(self):
        state = {
            "best_coverage": 50.0,
            "target_coverage": 100.0,
            "round": 2,
            "max_rounds": 10,
            "harness_slots": [{"status": "active"}, {"status": "converged"}],
            "coverage_plateau_count": 1,
        }
        self.assertEqual(_route_after_analyst(state), "planner")


if __name__ == "__main__":
    unittest.main()
