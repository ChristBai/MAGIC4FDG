"""Naive baseline: single-shot LLM fuzz driver generation.

No multi-strategy, no iteration, no coverage feedback, no research agent.
Just: read header → prompt LLM once → compile → fuzz → measure coverage.

This is the control group for benchmark comparison against FuzzForge.

Usage:
    python3 -m src.baselines.naive \
        --target-config targets/cjson.json \
        --fuzz-seconds 15 \
        --output-dir generated/baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import ROOT, load_target_config, resolve_project_path
from src.infra.docker_runner import compile_driver, run_fuzz_with_coverage
from src.infra.llm_factory import create_llm
from src.utils import strip_code_fences

PROMPT_TEMPLATE = (ROOT / "prompts" / "naive_baseline_prompt.txt").read_text(encoding="utf-8")


def _read_header(target_config: dict) -> str:
    header_path = resolve_project_path(target_config["header"])
    if header_path.exists():
        content = header_path.read_text(encoding="utf-8", errors="replace")
        return content[:4000]
    return "(header file not found)"


def _build_prompt(target_config: dict) -> str:
    header_content = _read_header(target_config)
    return (
        PROMPT_TEMPLATE
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{INCLUDE_DIRS}}", ", ".join(target_config.get("include_dirs", [])))
        .replace("{{HEADER_CONTENT}}", header_content)
    )


def run_naive_baseline(
    target_config_path: str,
    fuzz_seconds: int = 15,
    output_dir: str | None = None,
    temperature: float = 0.7,
) -> dict:
    """Run the naive baseline and return results."""
    config = load_target_config(Path(target_config_path))
    library_name = config.get("library_name", "unknown")

    print(f"[Naive Baseline] Target: {library_name}", flush=True)

    # Step 1: Generate driver with single LLM call
    prompt = _build_prompt(config)
    llm = create_llm(temperature=temperature)

    print("[Naive Baseline] Generating fuzz driver...", flush=True)
    response = llm.invoke([
        SystemMessage(content="You are an expert C/C++ security engineer. Output only valid C++ source code."),
        HumanMessage(content=prompt),
    ])
    raw_code = response.content if isinstance(response.content, str) else str(response.content)
    source_code = strip_code_fences(raw_code)

    if not source_code:
        print("[Naive Baseline] FAILED: empty response from LLM", flush=True)
        return {"error": "empty_response", "coverage_pct": 0.0}

    # Step 2: Compile
    print("[Naive Baseline] Compiling...", flush=True)
    success, errors = compile_driver(source_code, config, "naive_baseline.cpp")

    if not success:
        print(f"[Naive Baseline] Compile FAILED:\n{errors[:500]}", flush=True)
        return {
            "error": "compile_failed",
            "compile_errors": errors,
            "source_code": source_code,
            "coverage_pct": 0.0,
        }

    # Step 3: Fuzz + coverage
    print(f"[Naive Baseline] Fuzzing for {fuzz_seconds}s...", flush=True)
    cov_result = run_fuzz_with_coverage(
        source_code, config, fuzz_seconds=fuzz_seconds, driver_filename="naive_baseline_cov.cpp"
    )

    if cov_result.get("error"):
        print(f"[Naive Baseline] Coverage error: {cov_result['error'][:200]}", flush=True)

    totals = cov_result.get("coverage", {}).get("totals", {})
    lines = totals.get("lines", {})
    branches = totals.get("branches", {})

    line_count = lines.get("count", 0)
    line_covered = lines.get("covered", 0)
    branch_count = branches.get("count", 0)
    branch_covered = branches.get("covered", 0)

    line_pct = (line_covered / line_count * 100) if line_count > 0 else 0.0
    branch_pct = (branch_covered / branch_count * 100) if branch_count > 0 else 0.0

    print(f"[Naive Baseline] Coverage: {line_pct:.1f}% lines, {branch_pct:.1f}% branches", flush=True)

    result = {
        "library_name": library_name,
        "source_code": source_code,
        "compile_status": "ok",
        "coverage_pct": line_pct,
        "branch_coverage_pct": branch_pct,
        "line_count": line_count,
        "line_covered": line_covered,
        "fuzz_seconds": fuzz_seconds,
        "temperature": temperature,
    }

    # Save results
    if output_dir:
        out_path = Path(output_dir)
    else:
        out_path = ROOT / "generated" / "baseline" / library_name
    out_path.mkdir(parents=True, exist_ok=True)

    import time
    for attempt in range(3):
        try:
            (out_path / "driver.cpp").write_text(source_code, encoding="utf-8")
            (out_path / "result.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            break
        except TimeoutError:
            if attempt < 2:
                print(f"[Naive Baseline] Write timeout, retrying ({attempt+1}/3)...", flush=True)
                time.sleep(2)
            else:
                print(f"[Naive Baseline] Write failed after 3 attempts, printing result to stdout", flush=True)
                print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)

    print(f"[Naive Baseline] Results saved to {out_path}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Naive baseline fuzz driver generation")
    parser.add_argument("--target-config", required=True, help="Path to target config JSON")
    parser.add_argument("--fuzz-seconds", type=int, default=15, help="Fuzz duration (default: 15)")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--temperature", type=float, default=0.7, help="LLM temperature (default: 0.7)")

    args = parser.parse_args()
    result = run_naive_baseline(
        target_config_path=args.target_config,
        fuzz_seconds=args.fuzz_seconds,
        output_dir=args.output_dir,
        temperature=args.temperature,
    )

    if result.get("error"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
