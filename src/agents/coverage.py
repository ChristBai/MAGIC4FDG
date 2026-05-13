"""Coverage Agent: runs fuzz drivers and collects coverage with reachability analysis."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .docker_runner import run_fuzz_with_coverage, _docker_run
from .state import DriverVariant, PipelineState

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from target_config import ROOT


def _extract_uncovered_lines(coverage_report: dict) -> list[dict]:
    """Extract uncovered lines from llvm-cov export JSON."""
    uncovered = []
    files = coverage_report.get("coverage", {}).get("files", [])
    for file_info in files:
        filename = file_info.get("filename", "")
        summary = file_info.get("summary", {})
        lines_data = summary.get("lines", {})
        if not lines_data:
            continue

        segments = file_info.get("segments", [])
        if not segments:
            continue

        for seg in segments:
            if len(seg) >= 5 and seg[2] == 0 and seg[4]:
                uncovered.append({
                    "file": filename,
                    "line_no": seg[0],
                    "count": 0,
                    "reachable": True,
                })

    return uncovered


def _parse_cfg_output(cfg_text: str) -> dict[str, set[str]]:
    """Parse LLVM opt CFG output into adjacency list.

    Returns {block_label: set(successor_labels)}.
    """
    graph: dict[str, set[str]] = {}
    current_block = None

    for line in cfg_text.splitlines():
        block_match = re.match(r"^(\S+):.*$", line.strip())
        if block_match:
            current_block = block_match.group(1)
            if current_block not in graph:
                graph[current_block] = set()
            continue

        if current_block and "successor" in line.lower():
            succs = re.findall(r"%(\S+)", line)
            for s in succs:
                graph[current_block].add(s)
                if s not in graph:
                    graph[s] = set()

        br_match = re.match(r"\s*br\s+.*label\s+%(\S+)", line)
        if br_match and current_block:
            targets = re.findall(r"label\s+%([A-Za-z0-9_.]+)", line)
            for t in targets:
                graph[current_block].add(t)
                if t not in graph:
                    graph[t] = set()

    return graph


def _reachable_blocks(graph: dict[str, set[str]], entry: str) -> set[str]:
    """BFS from entry block to find all reachable basic blocks."""
    visited: set[str] = set()
    queue = [entry]
    while queue:
        block = queue.pop(0)
        if block in visited:
            continue
        visited.add(block)
        for succ in graph.get(block, set()):
            if succ not in visited:
                queue.append(succ)
    return visited


def _get_debug_line_mapping(ir_text: str) -> dict[str, list[int]]:
    """Map basic blocks to source line numbers from LLVM IR debug info.

    Returns {block_label: [line_numbers]}.
    """
    mapping: dict[str, list[int]] = {}
    current_block = "entry"
    mapping[current_block] = []

    for line in ir_text.splitlines():
        block_match = re.match(r"^(\S+):", line)
        if block_match:
            current_block = block_match.group(1)
            if current_block not in mapping:
                mapping[current_block] = []
            continue

        dbg_match = re.search(r"!dbg\s+!(\d+)", line)
        if dbg_match and current_block in mapping:
            pass

        line_match = re.search(r"!DILocation\(line:\s*(\d+)", line)
        if line_match and current_block in mapping:
            mapping[current_block].append(int(line_match.group(1)))

    return mapping


def _analyze_reachability(target_config: dict) -> set[int]:
    """Run LLVM reachability analysis to find reachable source lines.

    Uses clang to emit LLVM IR, then parses the CFG to determine
    which basic blocks (and thus source lines) are reachable from entry.
    Returns set of reachable line numbers, or empty set if analysis fails.
    """
    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])

    if not source_files:
        return set()

    primary_source = source_files[0]
    include_args = " ".join(f"-I{d}" for d in include_dirs)

    script = f"""#!/bin/bash
set -e
clang -S -emit-llvm -g -O0 {include_args} {primary_source} -o /tmp/target.ll 2>/dev/null
cat /tmp/target.ll
"""

    script_path = ROOT / "generated" / "reachability_analysis.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")

    try:
        result = _docker_run(["bash", "generated/reachability_analysis.sh"], timeout=30)
        script_path.unlink(missing_ok=True)

        if result.returncode != 0:
            return set()

        ir_text = result.stdout

        graph = _parse_cfg_output(ir_text)
        if not graph:
            return set()

        entry = "entry" if "entry" in graph else next(iter(graph), "")
        if not entry:
            return set()

        reachable = _reachable_blocks(graph, entry)

        reachable_lines: set[int] = set()
        for line in ir_text.splitlines():
            line_match = re.search(r"!DILocation\(line:\s*(\d+)", line)
            if line_match:
                reachable_lines.add(int(line_match.group(1)))

        return reachable_lines

    except Exception:
        script_path.unlink(missing_ok=True)
        return set()


def _mark_reachability(uncovered_lines: list[dict], reachable_lines: set[int]) -> list[dict]:
    """Mark each uncovered line with whether it's reachable from entry."""
    if not reachable_lines:
        return uncovered_lines

    for line_info in uncovered_lines:
        line_no = line_info.get("line_no", 0)
        line_info["reachable"] = line_no in reachable_lines

    return uncovered_lines


def _run_coverage_for_variant(
    variant: DriverVariant,
    target_config: dict,
    fuzz_seconds: int,
) -> DriverVariant:
    """Run fuzzing with coverage for a single compiled variant."""
    if variant["compile_status"] != "ok":
        return variant

    driver_filename = f"cov_{variant['id']}.cpp"
    report = run_fuzz_with_coverage(
        variant["source_code"],
        target_config,
        fuzz_seconds=fuzz_seconds,
        driver_filename=driver_filename,
    )

    if "error" in report and report.get("coverage_pct", 0) == 0:
        variant["coverage_pct"] = 0.0
        variant["branch_coverage_pct"] = 0.0
        variant["uncovered_lines"] = []
        return variant

    totals = report.get("coverage", {}).get("totals", {})
    lines_metric = totals.get("lines", {})
    branches_metric = totals.get("branches", {})

    line_count = lines_metric.get("count", 0)
    line_covered = lines_metric.get("covered", 0)
    variant["coverage_pct"] = (100.0 * line_covered / line_count) if line_count else 0.0

    branch_count = branches_metric.get("count", 0)
    branch_covered = branches_metric.get("covered", 0)
    variant["branch_coverage_pct"] = (100.0 * branch_covered / branch_count) if branch_count else 0.0

    variant["uncovered_lines"] = _extract_uncovered_lines(report)

    return variant


def coverage_node(state: PipelineState) -> dict:
    """LangGraph node: run coverage collection for all compiled variants."""
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    fuzz_seconds = state.get("fuzz_seconds", 15)
    messages = list(state.get("messages", []))
    iteration = state.get("iteration", 0) + 1

    messages.append(f"[Coverage] Starting iteration {iteration}")

    reachable_lines = _analyze_reachability(target_config)
    if reachable_lines:
        messages.append(f"[Coverage] Reachability analysis found {len(reachable_lines)} reachable lines")

    covered_variants: list[DriverVariant] = []
    for variant in variants:
        if variant["compile_status"] != "ok":
            covered_variants.append(variant)
            continue

        result = _run_coverage_for_variant(variant, target_config, fuzz_seconds)

        if reachable_lines:
            result["uncovered_lines"] = _mark_reachability(
                result.get("uncovered_lines", []), reachable_lines
            )

        covered_variants.append(result)
        messages.append(
            f"[Coverage] {result['id']}: line={result['coverage_pct']:.1f}% "
            f"branch={result['branch_coverage_pct']:.1f}%"
        )

    best_variant = max(
        (v for v in covered_variants if v["compile_status"] == "ok"),
        key=lambda v: v["coverage_pct"],
        default=None,
    )

    best_coverage = best_variant["coverage_pct"] if best_variant else 0.0
    best_driver = best_variant["source_code"] if best_variant else ""

    messages.append(f"[Coverage] Best coverage: {best_coverage:.1f}%")

    return {
        "variants": covered_variants,
        "iteration": iteration,
        "best_coverage": best_coverage,
        "best_driver": best_driver,
        "reachable_branches": [{"line": ln} for ln in sorted(reachable_lines)[:100]],
        "messages": messages,
    }
