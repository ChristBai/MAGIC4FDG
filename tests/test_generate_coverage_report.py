#!/usr/bin/env python3
"""Unit tests for coverage report formatting helpers."""

from __future__ import annotations

import unittest

from src.generate_coverage_report import metric_percent, parse_fuzzer_log, summarize_export


class CoverageReportHelpersTest(unittest.TestCase):
    def test_metric_percent_computes_when_missing(self) -> None:
        self.assertEqual(metric_percent({"count": 4, "covered": 1}), 25.0)

    def test_summarize_export_keeps_totals_and_files(self) -> None:
        export_data = {
            "data": [
                {
                    "totals": {"lines": {"count": 10, "covered": 7, "percent": 70.0}},
                    "files": [
                        {
                            "filename": "b.c",
                            "summary": {"lines": {"count": 2, "covered": 1, "percent": 50.0}},
                        },
                        {
                            "filename": "a.c",
                            "summary": {"lines": {"count": 8, "covered": 6, "percent": 75.0}},
                        },
                    ],
                }
            ]
        }

        summary = summarize_export(export_data)

        self.assertEqual(summary["totals"]["lines"]["covered"], 7)
        self.assertEqual([item["filename"] for item in summary["files"]], ["a.c", "b.c"])

    def test_parse_fuzzer_log_extracts_run_summary(self) -> None:
        log = """
#123 NEW cov: 10 ft: 12 corp: 3/44b lim: 18
###### Recommended dictionary. ######
"false" # Uses: 5
"\\u" # Uses: 2
###### End of recommended dictionary. ######
Done 456 runs in 7 second(s)
"""

        summary = parse_fuzzer_log(log)

        self.assertEqual(summary["executions"], 456)
        self.assertEqual(summary["final_corpus_files"], 3)
        self.assertEqual(summary["final_corpus_bytes"], 44)
        self.assertEqual(summary["recommended_dictionary"], ['"false"', '"\\u"'])


if __name__ == "__main__":
    unittest.main()
