"""报告生成：从 pipeline 最终状态生成 Markdown 和 JSON 报告。

每次运行产出两个文件：
- report.md：人类可读的摘要，包含变体对比表、覆盖率统计、
  未覆盖行分析、token 用量明细、执行日志
- report.json：机器可读的结构化指标，供自动化分析使用
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import ROOT


def _calc_union_branch(state: dict) -> str:
    """格式化 union 分支覆盖率显示。"""
    variants = state.get("variants", [])
    compiled = [v for v in variants if v.get("compile_status") == "ok"]
    if not compiled:
        return "N/A"
    best_branch = max(v.get("branch_coverage_pct", 0.0) for v in compiled)
    return f"{best_branch:.1f}% (best single variant)"


def generate_report(state: dict) -> str:
    """从最终 pipeline 状态生成 Markdown 报告。"""
    target_config = state.get("target_config", {})
    library_name = target_config.get("library_name", "unknown")

    lines = [
        f"# MAGIC4FDG Report: {library_name}",
        "",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Library**: `{library_name}`",
        f"**Description**: {target_config.get('description', '')}",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Rounds | {state.get('round', 0)} |",
        f"| Target coverage | {state.get('target_coverage', 100.0):.1f}% |",
        f"| **Union line coverage** | **{state.get('best_coverage', 0.0):.1f}%** ({state.get('union_line_covered', 0)}/{state.get('union_line_total', 0)} lines) |",
        f"| Union branch coverage | {_calc_union_branch(state)} |",
        f"| Fuzz seconds per variant | {state.get('fuzz_seconds', 15)} |",
    ]

    variants = state.get("all_variants", []) or state.get("variants", [])
    compiled = [v for v in variants if v.get("compile_status") == "ok"]
    failed = [v for v in variants if v.get("compile_status") == "failed"]

    lines.extend([
        f"| Total variants | {len(variants)} |",
        f"| Compiled successfully | {len(compiled)} |",
        f"| Failed to compile | {len(failed)} |",
        "",
    ])

    reached = state.get("best_coverage", 0) >= state.get("target_coverage", 100.0)
    lines.append(f"**Result**: {'Target coverage REACHED' if reached else 'Target coverage NOT reached'}")
    lines.append("")

    lines.extend(_variant_table(variants))
    lines.extend(_coverage_details(compiled))
    lines.extend(_token_usage(state.get("token_usage", {})))
    lines.extend(_execution_log(state.get("messages", [])))

    return "\n".join(lines)


def _variant_table(variants: list[dict]) -> list[str]:
    """生成变体对比表（按覆盖率降序排列）。"""
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
    """生成编译成功变体的覆盖率详情（top 3）。"""
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


def _token_usage(token_data: dict) -> list[str]:
    """格式化 token 用量统计（总量 + 按 agent 分解）。"""
    if not token_data or not token_data.get("total_tokens"):
        return []

    lines = [
        "## Token Usage",
        "",
        f"| Metric | Tokens |",
        f"|--------|--------|",
        f"| Prompt tokens | {token_data.get('total_prompt_tokens', 0):,} |",
        f"| Completion tokens | {token_data.get('total_completion_tokens', 0):,} |",
        f"| **Total tokens** | **{token_data.get('total_tokens', 0):,}** |",
        "",
    ]

    by_agent = token_data.get("by_agent", {})
    if by_agent:
        lines.append("| Agent | Calls | Prompt | Completion | Total |")
        lines.append("|-------|-------|--------|------------|-------|")
        for agent, stats in sorted(by_agent.items()):
            total = stats["prompt_tokens"] + stats["completion_tokens"]
            lines.append(
                f"| {agent} | {stats['calls']} "
                f"| {stats['prompt_tokens']:,} "
                f"| {stats['completion_tokens']:,} "
                f"| {total:,} |"
            )
        lines.append("")

    return lines


def _execution_log(messages: list[str]) -> list[str]:
    """格式化执行日志（最多展示最近 50 条）。"""
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


def save_report(state: dict, out_dir: Path | None = None) -> Path:
    """生成并保存报告（.md + .json），返回 report.md 路径。"""
    library_name = state.get("target_config", {}).get("library_name", "unknown")
    if out_dir is None:
        out_dir = ROOT / "generated" / "iterations" / library_name
    out_dir.mkdir(parents=True, exist_ok=True)

    report_md = generate_report(state)
    report_path = out_dir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    report_json = {
        "library_name": library_name,
        "description": state.get("target_config", {}).get("description", ""),
        "rounds": state.get("round", 0),
        "best_coverage": state.get("best_coverage", 0.0),
        "target_coverage": state.get("target_coverage", 100.0),
        "target_reached": state.get("best_coverage", 0) >= state.get("target_coverage", 100.0),
        "union_coverage": {
            "line_covered": state.get("union_line_covered", 0),
            "line_total": state.get("union_line_total", 0),
            "line_pct": state.get("best_coverage", 0.0),
            "branch_covered": state.get("union_branch_covered", 0),
            "branch_total": state.get("union_branch_total", 0),
            "branch_pct": (
                100.0 * state.get("union_branch_covered", 0) / state.get("union_branch_total", 1)
                if state.get("union_branch_total", 0) > 0
                else 0.0
            ),
        },
        "best_drivers": {
            slot.get("slot_id", ""): {
                "coverage_pct": slot.get("best_coverage", 0.0),
                "branch_coverage_pct": slot.get("best_branch_coverage", 0.0),
            }
            for slot in state.get("harness_slots", [])
            if slot.get("best_source")
        },
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
            for v in (state.get("all_variants", []) or state.get("variants", []))
        ],
        "token_usage": state.get("token_usage", {}),
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(report_json, indent=2, ensure_ascii=False), encoding="utf-8")

    return report_path
