#!/usr/bin/env python3
"""Unit tests for target_config.py."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from target_config import ROOT, load_target_config, resolve_project_path


class TestLoadTargetConfig(unittest.TestCase):
    def _write_config(self, data: dict, path: Path) -> Path:
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_valid_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            config = {
                "target_name": "test",
                "function_name": "foo",
                "source_files": ["a.c"],
                "include_dirs": ["inc"],
                "seed_corpus": "corpus",
            }
            json.dump(config, f)
            f.flush()
            result = load_target_config(Path(f.name))
            self.assertEqual(result["target_name"], "test")

    def test_raises_on_missing_field(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"target_name": "test"}, f)
            f.flush()
            with self.assertRaises(RuntimeError) as ctx:
                load_target_config(Path(f.name))
            self.assertIn("missing required fields", str(ctx.exception))

    def test_raises_on_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("not json")
            f.flush()
            with self.assertRaises(RuntimeError) as ctx:
                load_target_config(Path(f.name))
            self.assertIn("invalid target config JSON", str(ctx.exception))

    def test_raises_on_non_list_source_files(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            config = {
                "target_name": "test",
                "function_name": "foo",
                "source_files": "not_a_list",
                "include_dirs": ["inc"],
                "seed_corpus": "corpus",
            }
            json.dump(config, f)
            f.flush()
            with self.assertRaises(RuntimeError) as ctx:
                load_target_config(Path(f.name))
            self.assertIn("must be a list", str(ctx.exception))


class TestResolveProjectPath(unittest.TestCase):
    def test_relative_path_resolves_to_root(self) -> None:
        result = resolve_project_path("targets/test.json")
        self.assertEqual(result, ROOT / "targets/test.json")

    def test_absolute_path_unchanged(self) -> None:
        result = resolve_project_path("/tmp/test.json")
        self.assertEqual(result, Path("/tmp/test.json"))


if __name__ == "__main__":
    unittest.main()
