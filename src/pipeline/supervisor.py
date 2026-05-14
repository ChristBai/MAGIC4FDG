"""Supervisor: CLI entry point for the multi-agent fuzz driver pipeline.

Orchestrates the full pipeline: loads target config, initializes state with
temperature schedule, invokes the LangGraph, and saves results (best driver,
all variants, markdown/JSON reports) to generated/iterations/<library>/<timestamp>/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import ROOT, load_target_config
from src.pipeline.graph import compile_graph
from src.agents.generation import get_temperature_schedule
from src.infra.token_tracker import get_tracker
from src.pipeline.report import save_report


def run_pipeline(
    target_config_path: str,
    max_iterations: int = 3,
    target_coverage: float = 70.0,
    fuzz_seconds: int = 15,
    max_compile_retries: int = 3,
) -> dict:
    """Run the full multi-agent pipeline and return the final state."""
    config = load_target_config(Path(target_config_path))
    temp_schedule = get_temperature_schedule()

    initial_state = {
        "target_config": config,
        "target_config_path": target_config_path,
        "research_summary": "",
        "source_code_context": "",
        "reachable_branches": [],
        "variant_matrix": [],
        "variants": [],
        "iteration": 0,
        "max_iterations": max_iterations,
        "max_compile_retries": max_compile_retries,
        "target_coverage": target_coverage,
        "best_coverage": 0.0,
        "best_driver": "",
        "temperature_schedule": temp_schedule,
        "current_temp_idx": 0,
        "fuzz_seconds": fuzz_seconds,
        "final_report": {},
        "messages": [],
    }

    graph = compile_graph()
    get_tracker().reset()
    final_state = graph.invoke(initial_state)
    final_state["token_usage"] = get_tracker().summary()

    _save_results(final_state, config)
    return final_state


def _save_results(state: dict, config: dict) -> None:
    """Save pipeline results to the output directory."""
    from datetime import datetime

    target_name = config.get("library_name", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "generated" / "iterations" / target_name / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("best_driver"):
        driver_path = out_dir / "best_driver.cpp"
        driver_path.write_text(state["best_driver"], encoding="utf-8")

    variants_dir = out_dir / "variants"
    variants_dir.mkdir(exist_ok=True)
    for v in state.get("variants", []):
        if v.get("source_code"):
            vpath = variants_dir / f"{v['id']}.cpp"
            vpath.write_text(v["source_code"], encoding="utf-8")

    report_path = save_report(state, out_dir)

    latest_link = out_dir.parent / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(out_dir.name)

    _cleanup_temp_files()

    print(f"\n{'='*60}")
    print(f"Pipeline complete: {target_name}")
    print(f"  Iterations: {state.get('iteration', 0)}")
    print(f"  Best coverage: {state.get('best_coverage', 0.0):.1f}%")
    print(f"  Target: {state.get('target_coverage', 70.0):.1f}%")
    compiled = sum(1 for v in state.get("variants", []) if v["compile_status"] == "ok")
    print(f"  Variants compiled: {compiled}/{len(state.get('variants', []))}")
    token_usage = state.get("token_usage", {})
    if token_usage.get("total_tokens"):
        print(f"  Tokens used: {token_usage['total_tokens']:,} (prompt: {token_usage['total_prompt_tokens']:,}, completion: {token_usage['total_completion_tokens']:,})")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")


def _cleanup_temp_files() -> None:
    """Remove intermediate files from generated/ directory."""
    gen_dir = ROOT / "generated"
    for pattern in ["patch_*.cpp", "cov_*.cpp", "compile_test.sh",
                    "run_coverage.sh", "reachability_analysis.sh", "coverage_*.json"]:
        for f in gen_dir.glob(pattern):
            f.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-agent fuzz driver generation pipeline"
    )
    parser.add_argument(
        "--target-config", required=True,
        help="Path to target config JSON file",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=10,
        help="Max iterations cap, overrides temperature schedule length (default: 10)",
    )
    parser.add_argument(
        "--target-coverage", type=float, default=70.0,
        help="Target line coverage percentage (default: 70.0)",
    )
    parser.add_argument(
        "--fuzz-seconds", type=int, default=15,
        help="Seconds to fuzz each variant (default: 15)",
    )
    parser.add_argument(
        "--max-compile-retries", type=int, default=3,
        help="Max compilation fix attempts per variant (default: 3)",
    )

    args = parser.parse_args()

    final_state = run_pipeline(
        target_config_path=args.target_config,
        max_iterations=args.max_iterations,
        target_coverage=args.target_coverage,
        fuzz_seconds=args.fuzz_seconds,
        max_compile_retries=args.max_compile_retries,
    )

    reached = final_state.get("best_coverage", 0.0) >= final_state.get("target_coverage", 70.0)
    sys.exit(0 if reached else 1)


if __name__ == "__main__":
    main()
