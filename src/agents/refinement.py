"""Refinement Agent: fuses multiple variant coverage results into an improved driver.

Takes the current round's compiled variants + previous round's fused driver,
analyzes their coverage contributions, and asks an LLM to produce a single
fused driver that combines the best strategies from each. Also advances the
temperature index to trigger the next iteration round.

Note: Empirical testing shows cross-strategy fusion often produces "mediocre
averages" rather than improvements. Future work may replace this with
coverage-guided incremental generation.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.agents.coverage import load_source_context
from src.pipeline.state import DriverVariant, PipelineState
from src.utils import strip_code_fences

REFINEMENT_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "refinement_prompt.txt").read_text(
    encoding="utf-8"
)


def _build_variant_analysis(variants: list[DriverVariant]) -> str:
    """Build a summary of non-best variants' unique contributions."""
    lines = []
    compiled = [v for v in variants if v["compile_status"] == "ok"]
    compiled.sort(key=lambda v: v["coverage_pct"], reverse=True)

    # Skip the best (shown separately as BEST_DRIVER_CODE), show next 3
    other_variants = compiled[1:4]
    for i, v in enumerate(other_variants, 1):
        lines.append(f"### Variant {i}: {v['id']}")
        lines.append(f"- Line coverage: {v['coverage_pct']:.1f}%")
        lines.append(f"- Branch coverage: {v['branch_coverage_pct']:.1f}%")
        unique = v.get("unique_coverage", [])
        if unique:
            lines.append(f"- Unique lines covered (not by best): {unique[:10]}")
        lines.append(f"\n```cpp\n{v['source_code'][:1200]}\n```\n")

    if not other_variants:
        return "No other compiled variants."

    return "\n".join(lines)


def _build_uncovered_reachable(
    variants: list[DriverVariant],
    source_context: dict[str, dict[int, str]] | None = None,
) -> str:
    """Extract reachable but uncovered lines across all variants, with source code."""
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
        basename = Path(filename).name
        src_line = ""
        if source_context:
            src_line = source_context.get(basename, {}).get(line_no, "")
        if src_line:
            lines.append(f"- {basename}:{line_no}  →  {src_line.strip()}")
        else:
            lines.append(f"- {basename}:{line_no}")

    return "\n".join(lines)


def _build_function_coverage_summary(variants: list[DriverVariant]) -> str:
    """Build per-function coverage summary from the best variant's function_coverage data."""
    best = max(
        (v for v in variants if v["compile_status"] == "ok"),
        key=lambda v: v["coverage_pct"],
        default=None,
    )
    if not best:
        return "No function coverage data available."

    func_cov = best.get("function_coverage", [])
    if not func_cov:
        return "No function coverage data available."

    lines = []
    zero_cov = [f for f in func_cov if f.get("line_cover") == "0.00%"]
    high_cov = [f for f in func_cov if f.get("line_cover") not in ("0.00%", None) and float(f["line_cover"].rstrip("%")) >= 80]
    low_cov = [f for f in func_cov if f.get("line_cover") not in ("0.00%", None) and float(f["line_cover"].rstrip("%")) < 80]

    if zero_cov:
        lines.append(f"### Zero coverage ({len(zero_cov)} functions):")
        for f in zero_cov[:15]:
            lines.append(f"- {f['function']} ({f['lines_count']} lines)")

    if low_cov:
        lines.append(f"\n### Low coverage ({len(low_cov)} functions):")
        for f in sorted(low_cov, key=lambda x: float(x["line_cover"].rstrip("%")))[:10]:
            lines.append(f"- {f['function']}: {f['line_cover']} ({f['lines_miss']} lines uncovered)")

    if high_cov:
        lines.append(f"\n### Well covered ({len(high_cov)} functions):")
        for f in high_cov[:5]:
            lines.append(f"- {f['function']}: {f['line_cover']}")

    return "\n".join(lines) if lines else "No function coverage data available."


def _compute_unique_coverage(variants: list[DriverVariant]) -> list[DriverVariant]:
    """Compute unique coverage contribution for each variant."""
    compiled = [v for v in variants if v["compile_status"] == "ok"]

    coverage_sets: dict[str, set[int]] = {}
    for v in compiled:
        covered_lines: set[int] = set()
        for line_info in v.get("covered_lines", []):
            covered_lines.add(line_info.get("line_no", 0))
        coverage_sets[v["id"]] = covered_lines

    for v in compiled:
        other_coverage = set()
        for vid, lines in coverage_sets.items():
            if vid != v["id"]:
                other_coverage |= lines
        v["unique_coverage"] = sorted(coverage_sets[v["id"]] - other_coverage)

    return variants


def _build_coverage_feedback(
    variants: list[DriverVariant],
    source_context: dict[str, dict[int, str]] | None = None,
) -> str:
    """Build coverage feedback string for the next generation round."""
    best = max(
        (v for v in variants if v["compile_status"] == "ok"),
        key=lambda v: v["coverage_pct"],
        default=None,
    )
    if not best:
        return ""

    parts = []
    parts.append(f"Best coverage so far: {best['coverage_pct']:.1f}% line, {best['branch_coverage_pct']:.1f}% branch (variant: {best['id']})")
    parts.append(f"Strategy used: {best['config'].get('prompt_strategy', 'unknown')}")

    func_summary = _build_function_coverage_summary(variants)
    if func_summary and "No function coverage" not in func_summary:
        parts.append(f"\n### Function-Level Coverage Gaps\n{func_summary}")

    uncovered_text = _build_uncovered_reachable(variants, source_context)
    if uncovered_text and "No reachable uncovered" not in uncovered_text:
        parts.append(f"\n### Top Uncovered Reachable Lines (with source)\n{uncovered_text}")

    parts.append("\nFocus your driver on covering the UNCOVERED functions and lines listed above.")

    return "\n".join(parts)


def _render_refinement_prompt(
    target_config: dict,
    variants: list[DriverVariant],
    source_context: dict[str, dict[int, str]] | None = None,
) -> str:
    """Render the refinement prompt with best-plus-delta approach."""
    compiled = [v for v in variants if v["compile_status"] == "ok"]
    best = max(compiled, key=lambda v: v["coverage_pct"], default=None)

    best_code = best["source_code"] if best else ""
    best_cov = f"{best['coverage_pct']:.1f}" if best else "0.0"
    best_branch = f"{best['branch_coverage_pct']:.1f}" if best else "0.0"

    variant_analysis = _build_variant_analysis(variants)
    uncovered_reachable = _build_uncovered_reachable(variants, source_context)

    return (
        REFINEMENT_TEMPLATE
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{BEST_DRIVER_CODE}}", best_code)
        .replace("{{BEST_COVERAGE}}", best_cov)
        .replace("{{BEST_BRANCH_COVERAGE}}", best_branch)
        .replace("{{VARIANT_ANALYSIS}}", variant_analysis)
        .replace("{{UNCOVERED_REACHABLE}}", uncovered_reachable)
        .replace("{{FUNCTION_COVERAGE}}", _build_function_coverage_summary(variants))
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
    print(f"[Refinement] Fusing {len(fusion_candidates)} variants (round={current_idx})", flush=True)

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

    source_context = load_source_context(target_config)
    prompt = _render_refinement_prompt(target_config, fusion_candidates_updated, source_context)

    llm = create_llm(temperature=0.4)
    for attempt in range(3):
        try:
            response = llm.invoke([
                SystemMessage(content="You are an expert fuzz driver engineer specializing in coverage maximization."),
                HumanMessage(content=prompt),
            ])
            break
        except Exception as e:
            if attempt < 2:
                import time
                print(f"[Refinement] LLM call failed (attempt {attempt+1}/3): {e}, retrying...", flush=True)
                time.sleep(5 * (attempt + 1))
            else:
                print(f"[Refinement] LLM call failed after 3 attempts: {e}", flush=True)
                messages.append(f"[Refinement] LLM call failed: {e}")
                return {
                    "variants": variants,
                    "messages": messages,
                    "current_temp_idx": current_idx + 1,
                }

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
        "covered_lines": [],
        "function_coverage": [],
        "unique_coverage": [],
        "iteration": next_idx,
    }

    new_variants = variants + [fused_variant]
    messages.append(f"[Refinement] Fused {len(fusion_candidates_updated)} variants, advancing to temp_idx={next_idx}")

    coverage_feedback = _build_coverage_feedback(fusion_candidates_updated, source_context)

    return {
        "variants": new_variants,
        "messages": messages,
        "current_temp_idx": next_idx,
        "coverage_feedback": coverage_feedback,
    }
