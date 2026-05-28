"""Supervisor：MAGIC4FDG v2 流水线的 CLI 入口和顶层编排。

职责：
1. 解析命令行参数，加载目标库配置
2. 初始化 PipelineState（或从 checkpoint 恢复）
3. 调用 LangGraph 编译后的图执行完整流水线
4. 执行结束后保存结果（best_driver、variants、report）
5. 清理临时文件，输出摘要

使用方式：
  PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor \
    --target-config targets/cjson.json \
    --max-rounds 5 --target-coverage 100 --fuzz-seconds 60

支持 --resume 从最近的 checkpoint 恢复（适用于超时/崩溃后继续）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import ROOT, load_target_config
from src.infra.token_tracker import get_tracker
from src.pipeline.checkpoint import load_latest_checkpoint
from src.pipeline.graph import compile_graph
from src.pipeline.report import save_report


def run_pipeline(
    target_config_path: str,
    max_rounds: int = 10,
    target_coverage: float = 100.0,
    fuzz_seconds: int = 60,
    max_compile_retries: int = 3,
    temperature: float = 0.4,
    resume: bool = False,
) -> dict:
    """执行完整的多 agent 流水线，返回最终状态。

    流程：加载配置 → 初始化/恢复状态 → graph.invoke → 保存结果。
    """
    config = load_target_config(Path(target_config_path))
    lib_name = config.get("library_name", "unknown")

    # 从 checkpoint 恢复（适用于超时/崩溃后继续）
    # checkpoint 保存在 analyst 之前（round 尚未递增），恢复时需要 +1
    # 使 planner 进入 exploit 模式而非重新 explore
    if resume:
        checkpoint = load_latest_checkpoint(lib_name)
        if checkpoint:
            print(f"[Supervisor] Resuming from checkpoint (round {checkpoint.get('round', 0)})", flush=True)
            checkpoint["round"] = checkpoint.get("round", 0) + 1
            graph = compile_graph()
            get_tracker().reset()
            final_state = graph.invoke(checkpoint)
            final_state["token_usage"] = get_tracker().summary()
            _save_results(final_state, config)
            return final_state

    # 全新执行：构造初始状态
    initial_state = {
        "target_config": config,
        "target_config_path": target_config_path,
        "knowledge": {
            "api_entries": [],
            "call_graph": {},
            "type_definitions": [],
            "macro_constants": [],
            "slot_knowledge": {},
        },
        "strategy_selections": [],
        "harness_slots": [],
        "variants": [],
        "all_variants": [],
        "round": 0,
        "max_rounds": max_rounds,
        "max_compile_retries": max_compile_retries,
        "target_coverage": target_coverage,
        "best_coverage": 0.0,
        "best_driver": "",
        "best_drivers": {},
        "coverage_plateau_count": 0,
        "temperature": temperature,
        "union_line_covered": 0,
        "union_line_total": 0,
        "union_branch_covered": 0,
        "union_branch_total": 0,
        "coverage_analysis": {},
        "slot_coverage_analyses": {},
        "fuzz_seconds": fuzz_seconds,
        "checkpoint_dir": str(ROOT / "generated" / "checkpoints" / lib_name),
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
    """保存流水线结果到输出目录。

    输出结构：
      generated/iterations/<library>/<timestamp>/
        ├── best_driver.cpp      # 历史最佳 fuzz driver
        ├── variants/            # 最后一轮所有变体源码
        ├── report.md            # Markdown 报告
        └── report.json          # 结构化报告
      generated/iterations/<library>/latest → <timestamp>  (符号链接)
    """
    from datetime import datetime

    target_name = config.get("library_name", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "generated" / "iterations" / target_name / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("best_driver"):
        driver_path = out_dir / "best_driver.cpp"
        driver_path.write_text(state["best_driver"], encoding="utf-8")

    # 输出每个 slot 的 best driver，组成完整 driver 集合
    best_drivers = state.get("best_drivers", {})
    if best_drivers:
        drivers_dir = out_dir / "best_drivers"
        drivers_dir.mkdir(exist_ok=True)
        for slot_id, source in best_drivers.items():
            (drivers_dir / f"{slot_id}.cpp").write_text(source, encoding="utf-8")

    variants_dir = out_dir / "variants"
    variants_dir.mkdir(exist_ok=True)
    for v in state.get("variants", []):
        if v.get("source_code"):
            vpath = variants_dir / f"{v['id']}.cpp"
            vpath.write_text(v["source_code"], encoding="utf-8")

    save_report(state, out_dir)

    latest_link = out_dir.parent / "latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(out_dir.name)

    _cleanup_temp_files()

    print(f"\n{'='*60}")
    print(f"Pipeline complete: {target_name}")
    print(f"  Rounds: {state.get('round', 0)}")
    print(f"  Best coverage: {state.get('best_coverage', 0.0):.1f}%")
    print(f"  Target: {state.get('target_coverage', 100.0):.1f}%")
    all_v = state.get("all_variants", []) or state.get("variants", [])
    compiled = sum(1 for v in all_v if v["compile_status"] == "ok")
    print(f"  Variants compiled: {compiled}/{len(all_v)} (all rounds)")
    union_lc = state.get("union_line_covered", 0)
    union_lt = state.get("union_line_total", 0)
    union_bc = state.get("union_branch_covered", 0)
    union_bt = state.get("union_branch_total", 0)
    if union_lt:
        print(f"  Union lines: {union_lc}/{union_lt} ({100.0*union_lc/union_lt:.1f}%)")
    if union_bt:
        print(f"  Union branches: {union_bc}/{union_bt} ({100.0*union_bc/union_bt:.1f}%)")
    best_drivers = state.get("best_drivers", {})
    if best_drivers:
        print(f"  Best driver combination: {len(best_drivers)} drivers")
    token_usage = state.get("token_usage", {})
    if token_usage.get("total_tokens"):
        print(f"  Tokens used: {token_usage['total_tokens']:,} (prompt: {token_usage['total_prompt_tokens']:,}, completion: {token_usage['total_completion_tokens']:,})")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")


def _cleanup_temp_files() -> None:
    """清理 generated/ 目录下的中间临时文件。"""
    gen_dir = ROOT / "generated"
    for pattern in ["patch_*.cpp", "cov_*.cpp", "union_*.cpp", "compile_test.sh",
                    "run_coverage.sh", "run_union_coverage.sh",
                    "reachability_analysis.sh", "coverage_*.json", "union_coverage.json"]:
        for f in gen_dir.glob(pattern):
            f.unlink(missing_ok=True)
    for pattern in ["crash-*", "oom-*", "timeout-*"]:
        for f in ROOT.glob(pattern):
            f.unlink(missing_ok=True)


def main() -> None:
    """CLI 入口：解析参数并启动流水线。"""
    parser = argparse.ArgumentParser(
        description="MAGIC4FDG v2: multi-agent fuzz driver generation pipeline"
    )
    parser.add_argument(
        "--target-config", required=True,
        help="Path to target config JSON file",
    )
    parser.add_argument(
        "--max-rounds", type=int, default=10,
        help="Maximum rounds (default: 10)",
    )
    parser.add_argument(
        "--target-coverage", type=float, default=100.0,
        help="Target line coverage percentage (default: 100.0)",
    )
    parser.add_argument(
        "--fuzz-seconds", type=int, default=60,
        help="Seconds to fuzz each variant (default: 60)",
    )
    parser.add_argument(
        "--max-compile-retries", type=int, default=3,
        help="Max compilation fix attempts per variant (default: 3)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from latest checkpoint if available",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.4,
        help="LLM temperature for generation (default: 0.4)",
    )

    args = parser.parse_args()

    final_state = run_pipeline(
        target_config_path=args.target_config,
        max_rounds=args.max_rounds,
        target_coverage=args.target_coverage,
        fuzz_seconds=args.fuzz_seconds,
        max_compile_retries=args.max_compile_retries,
        temperature=args.temperature,
        resume=args.resume,
    )

    reached = final_state.get("best_coverage", 0.0) >= final_state.get("target_coverage", 100.0)
    sys.exit(0 if reached else 1)


if __name__ == "__main__":
    main()
