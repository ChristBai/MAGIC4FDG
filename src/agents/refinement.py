"""Refinement Agent: fuses multiple variant coverage into an improved driver."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.pipeline.state import DriverVariant, PipelineState
from src.utils import strip_code_fences

REFINEMENT_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "refinement_prompt.txt").read_text(
    encoding="utf-8"
)


def _build_variant_analysis(variants: list[DriverVariant]) -> str:
    """Build a summary of each variant's coverage for the LLM."""
    lines = []
    compiled = [v for v in variants if v["compile_status"] == "ok"]
    compiled.sort(key=lambda v: v["coverage_pct"], reverse=True)

    top_variants = compiled[:3]
    for i, v in enumerate(top_variants, 1):
        lines.append(f"### Variant {i}: {v['id']}")
        lines.append(f"- Line coverage: {v['coverage_pct']:.1f}%")
        lines.append(f"- Branch coverage: {v['branch_coverage_pct']:.1f}%")
        unique = v.get("unique_coverage", [])
        if unique:
            lines.append(f"- Unique lines covered: {unique[:10]}")
        lines.append(f"\n```cpp\n{v['source_code'][:1500]}\n```\n")

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
    for (filename, line_no), info in sorted(all_uncovered.items())[:30]:
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
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{VARIANT_ANALYSIS}}", variant_analysis)
        .replace("{{UNCOVERED_REACHABLE}}", uncovered_reachable)
        # Legacy placeholders
        .replace("{{FUNCTION_NAME}}", target_config.get("library_name", ""))
        .replace("{{FUNCTION_SIGNATURE}}", "")
    )


def refinement_node(state: PipelineState) -> dict:
    """LangGraph node: fuse current round variants + previous fused driver, then advance temperature.

    Fusion scope: all compiled variants from current round + previous round's fused driver.
    Also increments current_temp_idx to advance to the next temperature.
    """
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    messages = list(state.get("messages", []))
    current_idx = state.get("current_temp_idx", 0)

    current_round = [
        v for v in variants
        if v.get("iteration") == current_idx and v["compile_status"] == "ok"
    ]
    prev_fused = [
        v for v in variants
        if v.get("id", "").startswith("fused_") and v.get("iteration") == current_idx - 1
        and v["compile_status"] == "ok"
    ]
    fusion_candidates = current_round + prev_fused

    if not fusion_candidates:
        messages.append("[Refinement] No compiled variants to refine")
        return {
            "variants": variants,
            "messages": messages,
            "current_temp_idx": current_idx + 1,
        }

    variants = _compute_unique_coverage(variants)
    fusion_candidates_updated = [
        v for v in variants
        if v.get("iteration") == current_idx and v["compile_status"] == "ok"
    ] + [
        v for v in variants
        if v.get("id", "").startswith("fused_") and v.get("iteration") == current_idx - 1
        and v["compile_status"] == "ok"
    ]

    prompt = _render_refinement_prompt(target_config, fusion_candidates_updated)

    llm = create_llm(temperature=0.4)
    response = llm.invoke([
        SystemMessage(content="You are an expert fuzz driver engineer specializing in coverage maximization."),
        HumanMessage(content=prompt),
    ])

    prompt_tok, completion_tok = extract_token_usage(response)
    get_tracker().record("refinement", "default", prompt_tok, completion_tok)

    raw = response.content if isinstance(response.content, str) else str(response.content)
    fused_code = strip_code_fences(raw)

    next_idx = current_idx + 1
    fused_variant: DriverVariant = {
        "id": f"fused_iter{current_idx}",
        "config": {"model": "default", "prompt_strategy": "fused", "temperature": 0.4},
        "source_code": fused_code,
        "compile_status": "pending",
        "compile_errors": "",
        "patch_attempts": 0,
        "coverage_pct": 0.0,
        "branch_coverage_pct": 0.0,
        "uncovered_lines": [],
        "unique_coverage": [],
        "iteration": next_idx,
    }

    new_variants = variants + [fused_variant]
    messages.append(f"[Refinement] Fused {len(fusion_candidates_updated)} variants, advancing to temp_idx={next_idx}")

    return {
        "variants": new_variants,
        "messages": messages,
        "current_temp_idx": next_idx,
    }
