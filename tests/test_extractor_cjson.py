"""Integration test for knowledge extractor against cJSON library.

Requires: Docker running with magic4fdg:latest image.
Run: python3 -m pytest tests/test_extractor_cjson.py -v
"""
from __future__ import annotations

import time
import unittest

from src.knowledge.extractor import extract_knowledge

CJSON_CONFIG = {
    "library_name": "cjson",
    "header": "examples/cjson_lib/cJSON.h",
    "source_files": ["examples/cjson_lib/cJSON.c"],
    "include_dirs": ["examples/cjson_lib"],
    "language": "C",
}


def docker_available() -> bool:
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "images", "magic4fdg:latest", "-q"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


@unittest.skipUnless(docker_available(), "Docker or magic4fdg image not available")
class TestExtractorCJSON(unittest.TestCase):
    knowledge = None
    elapsed = 0.0

    @classmethod
    def setUpClass(cls):
        start = time.time()
        cls.knowledge = extract_knowledge(CJSON_CONFIG)
        cls.elapsed = time.time() - start
        print(f"\n[Setup] Extraction took {cls.elapsed:.1f}s")
        print(f"  APIs: {len(cls.knowledge['api_entries'])}")
        print(f"  Types: {len(cls.knowledge['type_definitions'])}")
        print(f"  Macros: {len(cls.knowledge['macro_constants'])}")
        print(f"  Call graph callers: {len(cls.knowledge['call_graph'])}")

    def test_performance_under_30s(self):
        self.assertLess(self.elapsed, 30.0)

    def test_api_count(self):
        """cJSON has ~78 public functions."""
        self.assertGreater(len(self.knowledge["api_entries"]), 60)

    def test_api_fields_complete(self):
        """Every APIEntry should have all required fields."""
        required = {"name", "signature", "return_type", "params", "category", "preconditions", "group"}
        for entry in self.knowledge["api_entries"]:
            self.assertTrue(required.issubset(entry.keys()), f"Missing fields in {entry.get('name')}")

    def test_categories_assigned(self):
        """At least parse, create, delete categories should exist."""
        categories = {e["category"] for e in self.knowledge["api_entries"]}
        for cat in ("parse", "create", "delete"):
            self.assertIn(cat, categories, f"Category '{cat}' not found")

    def test_no_all_utility(self):
        """Not everything should be 'utility' — categories should be diverse."""
        categories = [e["category"] for e in self.knowledge["api_entries"]]
        utility_pct = categories.count("utility") / len(categories)
        self.assertLess(utility_pct, 0.5, "Too many functions classified as utility")

    def test_macros_extracted(self):
        """Should extract cJSON type constants."""
        macros = self.knowledge["macro_constants"]
        self.assertGreater(len(macros), 5)
        names = {m["name"] for m in macros}
        self.assertIn("cJSON_Object", names)
        self.assertIn("cJSON_Array", names)

    def test_macros_have_kind(self):
        """Each macro should have name, value, kind fields."""
        for m in self.knowledge["macro_constants"]:
            self.assertIn("name", m)
            self.assertIn("value", m)
            self.assertIn("kind", m)
            self.assertIn(m["kind"], ("constant", "flag", "function_like"))

    def test_types_include_struct_cjson(self):
        """Should find struct cJSON."""
        names = {t["name"] for t in self.knowledge["type_definitions"] if t["kind"] == "struct"}
        self.assertIn("cJSON", names)

    def test_struct_cjson_has_fields(self):
        """struct cJSON should have known fields."""
        cjson_struct = next(
            (t for t in self.knowledge["type_definitions"]
             if t["kind"] == "struct" and t["name"] == "cJSON"),
            None,
        )
        self.assertIsNotNone(cjson_struct)
        field_names = {f["name"] for f in cjson_struct["fields"]}
        for expected in ("next", "prev", "child", "type", "valuestring"):
            self.assertIn(expected, field_names)

    def test_call_graph_not_empty(self):
        """Call graph should have entries from cJSON.c."""
        self.assertGreater(len(self.knowledge["call_graph"]), 0)

    def test_call_graph_deduplicated(self):
        """No duplicate callees in call graph."""
        for caller, callees in self.knowledge["call_graph"].items():
            self.assertEqual(len(callees), len(set(callees)),
                           f"Duplicates for {caller}: {callees}")

    def test_ownership_delete_functions(self):
        """cJSON_Delete should have transfer ownership on its param."""
        delete_fn = next(
            (e for e in self.knowledge["api_entries"] if e["name"] == "cJSON_Delete"),
            None,
        )
        self.assertIsNotNone(delete_fn, "cJSON_Delete not found")
        self.assertTrue(
            any(p.get("ownership") == "transfer" for p in delete_fn["params"]),
            f"cJSON_Delete params should have transfer ownership: {delete_fn['params']}",
        )

    def test_ownership_create_functions(self):
        """Create functions should have precondition about freeing."""
        create_fns = [e for e in self.knowledge["api_entries"] if "Create" in e["name"]]
        self.assertGreater(len(create_fns), 0)
        with_precond = [e for e in create_fns if any("free" in p.lower() for p in e["preconditions"])]
        self.assertGreater(len(with_precond), 0,
                          "At least some Create functions should note caller must free")

    def test_preconditions_from_comments(self):
        """At least some functions should have preconditions from doc comments."""
        with_preconditions = [e for e in self.knowledge["api_entries"] if e["preconditions"]]
        self.assertGreater(len(with_preconditions), 0,
                          "No functions have preconditions extracted")


if __name__ == "__main__":
    unittest.main()

