"""Refinement Agent: fuses multiple variant coverage into an improved driver."""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_factory import create_llm
from .state import DriverVariant, PipelineState

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_driver import strip_code_fences

REFINEMENT_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "refinement_prompt.txt").read_text(
    encoding="utf-8"
)


def _build_variant_analysis(variants: list[DriverVariant]) -> str:
    """Build a summary of each variant's coverage for the LLM."""
    lines = []
    compiled = [v for v in variants if v["compile_status"] == "ok"]
    compiled.sort(key=lambda v: v["coverage_pct"], reverse=True)

    for i, v in enumerate(compiled, 1):
        lines.append(f"### Variant {i}: {v['id']}")
        lines.append(f"- Line coverage: {v['coverage_pct']:.1f}%")
        lines.append(f"- Branch coverage: {v['branch_coverage_pct']:.1f}%")
        unique = v.get("unique_coverage", [])
        if unique:
            lines.append(f"- Unique lines covered: {unique[:20]}")
        lines.append(f"\n```cpp\n{v['source_code'][:3000]}\n```\n")

    return "\n".join(lines)


def _build_uncovered_reachable(variants: list[DriverVariant]) -> str:
    """Extract reachable but uncovered lines across all variants."""
    all_uncovered: dict[tuple[str, int], dict] = {}

    for v in variants:
        if v["compile_status"] != "ok":
            continue
        for line_info in v.get("uncovered_lines", []):
            if line_info.get("reachable", True):
                key = (line_info.get("file", ""), line_info.get("line_no", 0))
                if key not in all_uncovered:
                    all_uncovered[key] = line_info

    if not all_uncovered:
        return "No reachable uncovered lines identified."

    lines = []
    for (filename, line_no), info in sorted(all_uncovered.items())[:50]:
        lines.append(f"- {filename}:{line_no}")

    return "\n".join(lines)


def _compute_unique_coverage(variants: list[DriverVariant]) -> list[DriverVariant]:
    """Compute unique coverage contribution for each variant."""
    compiled = [v for v in variants if v["compile_status"] == "ok"]

    coverage_sets: dict[str, set[int]] = {}
    for v in compiled:
        covered_lines: set[int] = set()
        for line_info in v.get("uncovered_lines", []):
            pass
        coverage_sets[v["id"]] = covered_lines

    for v in compiled:
        other_coverage = set()
        for vid, lines in coverage_sets.items():
            if vid != v["id"]:
                other_coverage |= lines
        v["unique_coverage"] = sorted(coverage_sets[v["id"]] - other_coverage)

    return variants


def _render_refinement_prompt(target_config: dict, variants: list[DriverVariant]) -> str:
    """Render the refinement prompt with variant analysis."""
    variant_analysis = _build_variant_analysis(variants)
    uncovered_reachable = _build_uncovered_reachable(variants)

    return (
        REFINEMENT_TEMPLATE
        .replace("{{FUNCTION_NAME}}", target_config.get("function_name", ""))
        .replace("{{FUNCTION_SIGNATURE}}", target_config.get("signature", ""))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{VARIANT_ANALYSIS}}", variant_analysis)
        .replace("{{UNCOVERED_REACHABLE}}", uncovered_reachable)
    )


def refinement_node(state: PipelineState) -> dict:
    """LangGraph node: analyze variant coverage and produce a fused improved driver."""
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    messages = list(state.get("messages", []))

    compiled_variants = [v for v in variants if v["compile_status"] == "ok"]

    if not compiled_variants:
        messages.append("[Refinement] No compiled variants to refine")
        return {"variants": variants, "messages": messages}

    variants = _compute_unique_coverage(variants)

    prompt = _render_refinement_prompt(target_config, compiled_variants)

    llm = create_llm(temperature=0.4)
    response = llm.invoke([
        SystemMessage(content="You are an expert fuzz driver engineer specializing in coverage maximization."),
        HumanMessage(content=prompt),
    ])

    raw = response.content if isinstance(response.content, str) else str(response.content)
    fused_code = strip_code_fences(raw)

    fused_variant: DriverVariant = {
        "id": f"fused_iter{state.get('iteration', 1)}",
        "config": {"model": "gpt-4o", "prompt_strategy": "research", "temperature": 0.4},
        "source_code": fused_code,
        "compile_status": "pending",
        "compile_errors": "",
        "patch_attempts": 0,
        "coverage_pct": 0.0,
        "branch_coverage_pct": 0.0,
        "uncovered_lines": [],
        "unique_coverage": [],
    }

    new_variants = variants + [fused_variant]
    messages.append(f"[Refinement] Generated fused driver from {len(compiled_variants)} variants")

    return {
        "variants": new_variants,
        "messages": messages,
    }
