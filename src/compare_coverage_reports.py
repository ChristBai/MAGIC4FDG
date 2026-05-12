#!/usr/bin/env python3
"""Compare generated coverage report JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def metric_percent(report: dict[str, Any], metric_name: str) -> float:
    metric = report.get("coverage", {}).get("totals", {}).get(metric_name, {})
    percent = metric.get("percent")
    return float(percent) if isinstance(percent, (int, float)) else 0.0


def load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    report["_path"] = str(path)
    return report


def report_row(report: dict[str, Any]) -> dict[str, Any]:
    fuzzing = report.get("fuzzing", {})
    status = report.get("status", {})
    return {
        "target_name": report.get("target", {}).get("target_name", ""),
        "function_name": report.get("target", {}).get("function_name", ""),
        "line_percent": metric_percent(report, "lines"),
        "function_percent": metric_percent(report, "functions"),
        "region_percent": metric_percent(report, "regions"),
        "branch_percent": metric_percent(report, "branches"),
        "fuzz_seconds": report.get("fuzz_seconds"),
        "executions": fuzzing.get("executions"),
        "final_corpus_files": fuzzing.get("final_corpus_files"),
        "artifact_files": fuzzing.get("artifact_files"),
        "fuzz_exit_code": status.get("fuzz_exit_code"),
        "coverage_status": status.get("coverage"),
        "dictionary": report.get("dictionary", ""),
        "report_path": report.get("_path", ""),
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Target | Lines | Functions | Regions | Branches | Seconds | Execs | Corpus | Artifacts | Exit | Dict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {target} | {lines:.2f}% | {functions:.2f}% | {regions:.2f}% | {branches:.2f}% | "
            "{seconds} | {execs} | {corpus} | {artifacts} | {exit_code} | {dictionary} |".format(
                target=row["target_name"],
                lines=row["line_percent"],
                functions=row["function_percent"],
                regions=row["region_percent"],
                branches=row["branch_percent"],
                seconds=row["fuzz_seconds"],
                execs=row["executions"] or "",
                corpus=row["final_corpus_files"] or 0,
                artifacts=row["artifact_files"] or 0,
                exit_code=row["fuzz_exit_code"],
                dictionary=f"`{row['dictionary']}`" if row["dictionary"] else "",
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare coverage_report.json files.")
    parser.add_argument("reports", nargs="+", type=Path, help="Coverage report JSON files.")
    parser.add_argument("--json-out", type=Path, help="Optional path to write sorted JSON rows.")
    args = parser.parse_args()

    rows = [report_row(load_report(path)) for path in args.reports]
    rows.sort(
        key=lambda row: (
            row["line_percent"],
            row["function_percent"],
            row["branch_percent"],
        ),
        reverse=True,
    )

    print(markdown_table(rows))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"\nJSON comparison written to {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
