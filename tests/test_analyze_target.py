#!/usr/bin/env python3
"""Smoke checks for the rule-based target analyzer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AnalyzeTargetSmokeTest(unittest.TestCase):
    def test_cjson_parse_config_has_delete_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "analyze_target.py"),
                    "--library-name",
                    "cjson",
                    "--header",
                    str(ROOT / "examples" / "cjson_lib" / "cJSON.h"),
                    "--source",
                    "examples/cjson_lib/cJSON.c",
                    "--include-dir",
                    "examples/cjson_lib",
                    "--seed-corpus",
                    "examples/cjson_lib/seed_corpus",
                    "--out-dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            config = json.loads((out_dir / "cjson_cJSON_Parse.json").read_text(encoding="utf-8"))
            self.assertEqual(config["function_name"], "cJSON_Parse")
            self.assertEqual(config["cleanup_function"], "cJSON_Delete")

            index = json.loads((out_dir / "cjson_index.json").read_text(encoding="utf-8"))
            self.assertTrue(any(candidate["function_name"] == "cJSON_Parse" for candidate in index))


if __name__ == "__main__":
    unittest.main()
