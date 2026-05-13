"""Report generator: produces Markdown iteration reports from pipeline state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from target_config import ROOT


def generate_report(state: dict) -> str:
    """Generate a Markdown report from the final pipeline state."""
    target_config = state.get("target_config", {})
    target_name = target_config.get("target_name", "unknown")
    function_name = target_config.get("function_name", "unknown")

    lines = [
        f"# Fuzz Driver Generation Report: {target_name}",
        "",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Target function**: `{function_name}`",
        f"**Signature**: `{target_config.get('signature', '')}`",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Iterations | {state.get('iteration', 0)} |",
        f"| Target coverage | {state.get('target_coverage', 70.0):.1f}% |",
        f"| Best coverage achieved | {state.get('best_coverage', 0.0):.1f}% |",
        f"| Fuzz seconds per variant | {state.get('fuzz_seconds', 15)} |",
    ]

    variants = state.get("variants", [])
    compiled = [v for v in variants if v.get("compile_status") == "ok"]
    failed = [v for v in variants if v.get("compile_status") == "failed"]

    lines.extend([
        f"| Total variants | {len(variants)} |",
        f"| Compiled successfully | {len(compiled)} |",
        f"| Failed to compile | {len(failed)} |",
        "",
    ])

    reached = state.get("best_coverage", 0) >= state.get("target_coverage", 70.0)
    lines.append(f"**Result**: {'Target coverage REACHED' if reached else 'Target coverage NOT reached'}")
    lines.append("")

    lines.extend(_variant_table(variants))
    lines.extend(_coverage_details(compiled))
    lines.extend(_execution_log(state.get("messages", [])))

    return "\n".join(lines)


def _variant_table(variants: list[dict]) -> list[str]:
    """Generate the variant comparison table."""
    if not variants:
        return ["## Variants", "", "No variants generated.", ""]

    lines = [
        "## Variant Comparison",
        "",
        "| ID | Model | Strategy | Temp | Status | Line Cov | Branch Cov | Patches |",
        "|----|-------|----------|------|--------|----------|------------|---------|",
    ]

    sorted_variants = sorted(variants, key=lambda v: v.get("coverage_pct", 0), reverse=True)
    for v in sorted_variants:
        config = v.get("config", {})
        lines.append(
            f"| {v.get('id', '?')} "
            f"| {config.get('model', '?')} "
            f"| {config.get('prompt_strategy', '?')} "
            f"| {config.get('temperature', '?')} "
            f"| {v.get('compile_status', '?')} "
            f"| {v.get('coverage_pct', 0):.1f}% "
            f"| {v.get('branch_coverage_pct', 0):.1f}% "
            f"| {v.get('patch_attempts', 0)} |"
        )

    lines.append("")
    return lines


def _coverage_details(compiled_variants: list[dict]) -> list[str]:
    """Generate coverage details for compiled variants."""
    if not compiled_variants:
        return []

    lines = ["## Coverage Details", ""]

    top = sorted(compiled_variants, key=lambda v: v.get("coverage_pct", 0), reverse=True)[:3]
    for v in top:
        uncovered = v.get("uncovered_lines", [])
        reachable_uncovered = [u for u in uncovered if u.get("reachable", True)]
        unreachable = [u for u in uncovered if not u.get("reachable", True)]

        lines.append(f"### {v.get('id', '?')}")
        lines.append(f"- Line coverage: {v.get('coverage_pct', 0):.1f}%")
        lines.append(f"- Branch coverage: {v.get('branch_coverage_pct', 0):.1f}%")
        lines.append(f"- Reachable uncovered lines: {len(reachable_uncovered)}")
        lines.append(f"- Unreachable lines (filtered): {len(unreachable)}")
        lines.append("")

    return lines


def _execution_log(messages: list[str]) -> list[str]:
    """Format the execution log."""
    if not messages:
        return []

    lines = [
        "## Execution Log",
        "",
        "```",
    ]
    lines.extend(messages[-50:])
    lines.extend(["```", ""])
    return lines


def save_report(state: dict) -> Path:
    """Generate and save the report to the output directory."""
    target_name = state.get("target_config", {}).get("target_name", "unknown")
    out_dir = ROOT / "generated" / "iterations" / target_name
    out_dir.mkdir(parents=True, exist_ok=True)

    report_md = generate_report(state)
    report_path = out_dir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    report_json = {
        "target_name": target_name,
        "function_name": state.get("target_config", {}).get("function_name", ""),
        "iterations": state.get("iteration", 0),
        "best_coverage": state.get("best_coverage", 0.0),
        "target_coverage": state.get("target_coverage", 70.0),
        "target_reached": state.get("best_coverage", 0) >= state.get("target_coverage", 70.0),
        "variants": [
            {
                "id": v.get("id", ""),
                "model": v.get("config", {}).get("model", ""),
                "strategy": v.get("config", {}).get("prompt_strategy", ""),
                "temperature": v.get("config", {}).get("temperature", 0),
                "compile_status": v.get("compile_status", ""),
                "coverage_pct": v.get("coverage_pct", 0.0),
                "branch_coverage_pct": v.get("branch_coverage_pct", 0.0),
                "patch_attempts": v.get("patch_attempts", 0),
            }
            for v in state.get("variants", [])
        ],
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(report_json, indent=2, ensure_ascii=False), encoding="utf-8")

    return report_path
