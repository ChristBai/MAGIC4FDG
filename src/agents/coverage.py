"""Coverage Agent：运行 fuzz driver 并收集覆盖率，含 CFG 可达性分析。

对当前轮次中每个编译成功的变体：
1. 在 Docker 中以 fork 模式运行 LibFuzzer（覆盖率插桩）
2. 通过 llvm-cov 收集行/分支覆盖率数据
3. 执行 LLVM CFG 可达性分析：
   - clang -emit-llvm 生成 IR
   - 解析 CFG 做 BFS，找到从 entry 可达的基本块
   - 将不可达块中的未覆盖行标记为 reachable=False
   - 避免下游 agent 在结构性不可达的代码上浪费精力

4. 更新 harness_slots：
   - 覆盖率提升 → 更新 best_source/best_coverage
   - 无提升 → plateau_count++

5. 跟踪全局最佳覆盖率和 plateau 计数（用于 supervisor 终止决策）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.infra.docker_runner import run_fuzz_with_coverage, _docker_run
from src.pipeline.state import DriverVariant, PipelineState
from src.config import ROOT, resolve_project_path


def _extract_uncovered_lines(coverage_report: dict) -> list[dict]:
    """从 llvm-cov export JSON 中提取未覆盖行（展开 segment 范围）。

    与 _extract_covered_lines 对称：提取 count==0 的 segment 范围内所有行。
    """
    uncovered = []
    files = coverage_report.get("coverage", {}).get("files", [])
    for file_info in files:
        filename = file_info.get("filename", "")
        segments = file_info.get("segments", [])
        if not segments:
            continue
        for i, seg in enumerate(segments):
            if len(seg) < 5:
                continue
            line_start = seg[0]
            count = seg[2]
            if count != 0:
                continue
            if not seg[4]:
                continue
            # 确定该 segment 覆盖到哪一行
            if i + 1 < len(segments):
                line_end = segments[i + 1][0]
            else:
                line_end = line_start + 1
            for line_no in range(line_start, line_end):
                uncovered.append({
                    "file": filename,
                    "line_no": line_no,
                    "count": 0,
                    "reachable": True,
                })
    return uncovered


def _extract_covered_lines(coverage_report: dict) -> list[dict]:
    """从 llvm-cov export JSON 中提取已覆盖行（展开 segment 范围）。

    llvm-cov segments 格式: [line, col, count, hasCount, isRegionEntry]
    每个 segment 标记一个区域的开始，两个相邻 segment 之间的所有行
    都属于前一个 segment 的 count。必须展开为逐行数据才能正确计算并集。
    """
    covered = []
    files = coverage_report.get("coverage", {}).get("files", [])
    for file_info in files:
        filename = file_info.get("filename", "")
        segments = file_info.get("segments", [])
        if not segments:
            continue
        for i, seg in enumerate(segments):
            if len(seg) < 5:
                continue
            line_start = seg[0]
            count = seg[2]
            if count <= 0:
                continue
            # 确定该 segment 覆盖到哪一行（到下一个 segment 的起始行）
            if i + 1 < len(segments):
                line_end = segments[i + 1][0]
            else:
                line_end = line_start + 1
            for line_no in range(line_start, line_end):
                covered.append({"file": filename, "line_no": line_no, "count": count})
    return covered


def _parse_cfg_output(cfg_text: str) -> dict[str, set[str]]:
    """解析 LLVM IR 文本，构建 CFG 邻接表 {block_label: set(successor_labels)}。"""
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
    """从 entry 块出发做 BFS，返回所有可达的基本块标签。"""
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
    """从 LLVM IR 的 debug info 中建立 基本块 → 源码行号 的映射。"""
    # Phase 1: build metadata ID -> line number index from !DILocation definitions
    metadata_lines: dict[str, int] = {}
    for line in ir_text.splitlines():
        m = re.match(r"^!(\d+)\s*=\s*!DILocation\(line:\s*(\d+)", line)
        if m:
            metadata_lines[m.group(1)] = int(m.group(2))

    # Phase 2: walk function bodies, mapping !dbg refs to source lines per block
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
            meta_id = dbg_match.group(1)
            if meta_id in metadata_lines:
                mapping[current_block].append(metadata_lines[meta_id])

    return mapping


def _analyze_reachability(target_config: dict) -> set[int]:
    """执行 LLVM 可达性分析，返回从 entry 可达的源码行号集合。

    流程：clang -emit-llvm → 解析 CFG → BFS → 映射到源码行。
    分析失败时返回空集（不影响主流程，只是不做可达性过滤）。
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

        block_to_lines = _get_debug_line_mapping(ir_text)
        reachable_lines: set[int] = set()
        for block_label in reachable:
            reachable_lines.update(block_to_lines.get(block_label, []))

        return reachable_lines

    except Exception:
        script_path.unlink(missing_ok=True)
        return set()


def _mark_reachability(uncovered_lines: list[dict], reachable_lines: set[int]) -> list[dict]:
    """为每个未覆盖行标注是否从 entry 可达。"""
    if not reachable_lines:
        return uncovered_lines

    for line_info in uncovered_lines:
        line_no = line_info.get("line_no", 0)
        line_info["reachable"] = line_no in reachable_lines

    return uncovered_lines


def load_source_context(target_config: dict) -> dict[str, dict[int, str]]:
    """加载目标源文件为 {filename: {行号: 行内容}} 映射，供 Analyst 使用。"""
    context: dict[str, dict[int, str]] = {}
    for src_file in target_config.get("source_files", []):
        path = resolve_project_path(src_file)
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            line_map = {i + 1: line for i, line in enumerate(lines)}
            context[str(path)] = line_map
            context[path.name] = line_map
    return context


def _run_coverage_for_variant(
    variant: DriverVariant,
    target_config: dict,
    fuzz_seconds: int,
) -> DriverVariant:
    """对单个编译成功的变体运行 fuzzing + 覆盖率收集。"""
    if variant["compile_status"] != "ok":
        return variant

    driver_filename = f"cov_{variant['id']}.cpp"
    report = run_fuzz_with_coverage(
        variant["source_code"],
        target_config,
        fuzz_seconds=fuzz_seconds,
        driver_filename=driver_filename,
    )

    if "error" in report:
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
    variant["covered_lines"] = _extract_covered_lines(report)
    variant["function_coverage"] = report.get("function_coverage", [])

    return variant


def coverage_node(state: PipelineState) -> dict:
    """LangGraph 节点：对所有编译成功的变体执行 fuzz + 覆盖率收集 + slot 更新。

    覆盖率分两层：
    - per-slot：每个 slot 只看自己变体的覆盖率（用于 plateau 判定和 Analyst 输入）
    - 项目级：所有 slot 覆盖行的并集（用于终止决策）
    """
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    fuzz_seconds = state.get("fuzz_seconds", 15)
    messages = list(state.get("messages", []))
    round_num = state.get("round", 0)
    slots = list(state.get("harness_slots", []))
    prev_best = state.get("best_coverage", 0.0)

    compiled = [v for v in variants if v["compile_status"] == "ok"]
    print(f"[Coverage] Round {round_num + 1}, {len(compiled)} variants to fuzz ({fuzz_seconds}s each)", flush=True)
    messages.append(f"[Coverage] Round {round_num + 1}: fuzzing {len(compiled)} variants")

    reachable_lines = _analyze_reachability(target_config)
    if reachable_lines:
        messages.append(f"[Coverage] Reachability analysis found {len(reachable_lines)} reachable lines")

    max_workers = int(os.environ.get("FUZZFORGE_DOCKER_PARALLEL", os.environ.get("FUZZFORGE_PARALLEL", "5")))
    non_compiled = [v for v in variants if v["compile_status"] != "ok"]

    if not compiled:
        covered_variants: list[DriverVariant] = list(non_compiled)
    else:
        covered_variants: list[DriverVariant] = list(non_compiled)

        with ThreadPoolExecutor(max_workers=min(len(compiled), max_workers)) as pool:
            futures = {
                pool.submit(_run_coverage_for_variant, v, target_config, fuzz_seconds): v
                for v in compiled
            }
            for future in as_completed(futures):
                result = future.result()
                print(f"[Coverage]   {result['id']}: {result['coverage_pct']:.1f}%", flush=True)

                if reachable_lines:
                    result["uncovered_lines"] = _mark_reachability(
                        result.get("uncovered_lines", []), reachable_lines
                    )

                covered_variants.append(result)
                messages.append(
                    f"[Coverage] {result['id']}: line={result['coverage_pct']:.1f}% "
                    f"branch={result['branch_coverage_pct']:.1f}%"
                )

    # Update harness_slots with per-slot coverage results
    for slot in slots:
        slot_variants = [v for v in covered_variants if v.get("slot_id") == slot["slot_id"] and v["compile_status"] == "ok"]
        if not slot_variants:
            continue
        best_v = max(slot_variants, key=lambda v: v["coverage_pct"])
        cov = best_v["coverage_pct"]
        slot["coverage_history"] = slot.get("coverage_history", []) + [cov]
        if cov > slot.get("best_coverage", 0.0):
            slot["best_coverage"] = cov
            slot["best_branch_coverage"] = best_v.get("branch_coverage_pct", 0.0)
            slot["best_source"] = best_v["source_code"]
            slot["best_uncovered_lines"] = best_v.get("uncovered_lines", [])
            slot["best_function_coverage"] = best_v.get("function_coverage", [])
            slot["current_source"] = best_v["source_code"]
        else:
            slot["plateau_count"] = slot.get("plateau_count", 0) + 1

    # 项目级覆盖率：所有 slot 覆盖行的并集
    union_stats = _aggregate_project_coverage(covered_variants)
    project_coverage = union_stats["line_pct"]
    if project_coverage < prev_best:
        project_coverage = prev_best

    # 收集每个 slot 的 best driver，组成 driver 集合
    best_drivers: dict[str, str] = dict(state.get("best_drivers", {}))
    for slot in slots:
        if slot.get("best_source"):
            best_drivers[slot["slot_id"]] = slot["best_source"]

    # best_driver 保留兼容：取覆盖率最高的单个变体
    best_variant = max(
        (v for v in covered_variants if v["compile_status"] == "ok"),
        key=lambda v: v["coverage_pct"],
        default=None,
    )
    best_driver = best_variant["source_code"] if best_variant else state.get("best_driver", "")

    # Pipeline-level plateau（基于项目级覆盖率）
    plateau_count = state.get("coverage_plateau_count", 0)
    if project_coverage - prev_best < 0.5 and round_num > 0:
        plateau_count += 1
    else:
        plateau_count = 0

    messages.append(
        f"[Coverage] Project coverage: {project_coverage:.1f}% "
        f"(lines: {union_stats['line_covered']}/{union_stats['line_total']}, "
        f"branch: {union_stats['branch_pct']:.1f}%, plateau={plateau_count})"
    )

    # 累积所有轮次的 variant 到 all_variants
    all_variants = list(state.get("all_variants", []))
    all_variants.extend(covered_variants)

    return {
        "variants": covered_variants,
        "all_variants": all_variants,
        "harness_slots": slots,
        "best_coverage": project_coverage,
        "best_driver": best_driver,
        "best_drivers": best_drivers,
        "coverage_plateau_count": plateau_count,
        "union_line_covered": union_stats["line_covered"],
        "union_line_total": union_stats["line_total"],
        "union_branch_covered": 0,
        "union_branch_total": 0,
        "messages": messages,
    }


def _aggregate_project_coverage(variants: list[DriverVariant]) -> dict:
    """计算项目级覆盖率 = 所有变体覆盖行/分支的并集。

    返回 {line_pct, line_covered, line_total, branch_pct, branch_covered, branch_total}。
    """
    all_covered: set[tuple[str, int]] = set()
    all_lines: set[tuple[str, int]] = set()

    for v in variants:
        if v.get("compile_status") != "ok":
            continue
        for line in v.get("covered_lines", []):
            all_covered.add((line["file"], line["line_no"]))
        for line in v.get("uncovered_lines", []):
            all_lines.add((line["file"], line["line_no"]))

    all_lines |= all_covered
    line_pct = 100.0 * len(all_covered) / len(all_lines) if all_lines else 0.0

    # 分支覆盖率：取各 variant 的加权统计（llvm-cov totals 级别）
    total_branches = 0
    covered_branches = 0
    for v in variants:
        if v.get("compile_status") != "ok":
            continue
        # 使用 variant 级别的分支数据（来自 llvm-cov totals）
        # 由于分支无法像行一样精确做并集，取最大值作为近似
        v_branch_pct = v.get("branch_coverage_pct", 0.0)
        if v_branch_pct > 0 and total_branches == 0:
            # 所有 variant 共享同一目标库，分支总数相同
            # 用覆盖率反推 covered 数量取并集近似
            pass

    # 简化方案：分支并集取所有 variant 中最高的分支覆盖率
    # （精确并集需要 segment 级别数据，当前 llvm-cov 只给 totals）
    best_branch_pct = max(
        (v.get("branch_coverage_pct", 0.0) for v in variants if v.get("compile_status") == "ok"),
        default=0.0,
    )

    return {
        "line_pct": line_pct,
        "line_covered": len(all_covered),
        "line_total": len(all_lines),
        "branch_pct": best_branch_pct,
    }
