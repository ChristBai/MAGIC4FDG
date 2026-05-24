"""Analyst Agent：诊断覆盖率缺口，产出结构化约束（per-slot 隔离）。

对每个 active slot 独立运行分析：
1. 读取该 slot 的 best_uncovered_lines（只看自己职责范围内的覆盖率）
2. 结合源码上下文，诊断 WHY 这些行未被覆盖
3. 输出结构化约束（preconditions、required state、input patterns）
4. 更新该 slot 的 SlotKnowledge（隔离，不影响其他 slot）

设计原则：
- 每个 slot 只看自己 target_apis 对应的覆盖率数据
- 约束和模式按 slot_id 隔离存储
- 不会出现"让 parse driver 去覆盖 lifecycle 代码"的错误引导
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.knowledge.context_builder import build_analyst_context
from src.agents.coverage import load_source_context
from src.pipeline.state import HarnessSlot, KnowledgeStore, PipelineState
from src.utils import strip_code_fences

ANALYST_PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / "analyst_prompt.txt").read_text(encoding="utf-8")


def analyst_node(state: PipelineState) -> dict:
    """LangGraph 节点：对每个 active slot 独立运行覆盖率缺口分析。"""
    target_config = state["target_config"]
    knowledge = state.get("knowledge", {})
    slots = state.get("harness_slots", [])
    messages = list(state.get("messages", []))
    round_num = state.get("round", 0)

    active_slots = [s for s in slots if s.get("status") == "active"]
    print(f"[Analyst] Round {round_num + 1}, analyzing {len(active_slots)} active slots", flush=True)

    if not active_slots:
        messages.append("[Analyst] No active slots to analyze")
        return {
            "slot_coverage_analyses": {},
            "knowledge": knowledge,
            "round": round_num + 1,
            "messages": messages,
        }

    source_context = load_source_context(target_config)
    slot_analyses: dict[str, dict] = {}
    updated_knowledge = dict(knowledge)
    slot_knowledge = dict(updated_knowledge.get("slot_knowledge", {}))

    max_workers = int(os.environ.get("MAGIC4FDG_LLM_PARALLEL", os.environ.get("MAGIC4FDG_PARALLEL", "10")))

    def _analyze_one(slot):
        analysis = _analyze_single_slot(slot, knowledge, target_config, source_context)
        return slot["slot_id"], analysis

    with ThreadPoolExecutor(max_workers=min(len(active_slots), max_workers)) as pool:
        futures = [pool.submit(_analyze_one, slot) for slot in active_slots]
        for future in as_completed(futures):
            slot_id, analysis = future.result()
            slot_analyses[slot_id] = analysis

            slot_knowledge[slot_id] = _update_slot_knowledge(
                slot_knowledge.get(slot_id, {}), analysis, round_num
            )

            n_constraints = len(analysis.get("constraints", []))
            n_clusters = len(analysis.get("uncovered_clusters", []))
            messages.append(
                f"[Analyst] {slot_id}: {n_constraints} constraints, {n_clusters} clusters"
            )
            print(f"[Analyst]   {slot_id}: {n_constraints} constraints, {n_clusters} clusters", flush=True)

    updated_knowledge["slot_knowledge"] = slot_knowledge

    return {
        "slot_coverage_analyses": slot_analyses,
        "coverage_analysis": slot_analyses.get(active_slots[0]["slot_id"], {}),
        "knowledge": updated_knowledge,
        "round": round_num + 1,
        "messages": messages,
    }


def _analyze_single_slot(
    slot: HarnessSlot,
    knowledge: KnowledgeStore,
    target_config: dict,
    source_context: dict[str, dict[int, str]] | None,
) -> dict:
    """对单个 slot 运行 LLM 分析，返回结构化约束。"""
    best_source = slot.get("best_source", "")
    best_coverage = slot.get("best_coverage", 0.0)
    best_branch = slot.get("best_branch_coverage", 0.0)
    uncovered_lines = slot.get("best_uncovered_lines", [])
    function_coverage = slot.get("best_function_coverage", [])

    if not best_source and not uncovered_lines:
        return {"constraints": [], "uncovered_clusters": [], "knowledge_updates": {}}

    analyst_context = build_analyst_context(knowledge, source_context)
    uncovered_summary = _build_uncovered_summary(uncovered_lines, source_context)
    function_summary = _build_function_summary(function_coverage)

    prompt = ANALYST_PROMPT.replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
    prompt = prompt.replace("{{BEST_COVERAGE}}", f"{best_coverage:.1f}")
    prompt = prompt.replace("{{BEST_BRANCH_COVERAGE}}", f"{best_branch:.1f}")
    prompt = prompt.replace("{{BEST_DRIVER_CODE}}", best_source)
    prompt = prompt.replace("{{UNCOVERED_LINES}}", uncovered_summary)
    prompt = prompt.replace("{{KNOWLEDGE_CONTEXT}}", analyst_context)
    prompt = prompt.replace("{{FUNCTION_COVERAGE}}", function_summary)

    llm = create_llm(temperature=0.3)
    try:
        response = llm.invoke([
            SystemMessage(content="You are an expert code coverage analyst. Output valid JSON only."),
            HumanMessage(content=prompt),
        ])
    except Exception as e:
        print(f"[Analyst]   {slot['slot_id']} LLM failed: {e}", flush=True)
        return {"constraints": [], "uncovered_clusters": [], "knowledge_updates": {}}

    prompt_tok, completion_tok = extract_token_usage(response)
    get_tracker().record("analyst", "default", prompt_tok, completion_tok)

    raw = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_analyst_response(raw)


def _build_uncovered_summary(uncovered_lines: list[dict], source_context: dict | None) -> str:
    """构建未覆盖行摘要（含源码上下文）。"""
    lines = []
    for line_info in uncovered_lines[:30]:
        filename = Path(line_info.get("file", "")).name
        line_no = line_info.get("line_no", 0)
        src_line = ""
        if source_context:
            src_line = source_context.get(filename, {}).get(line_no, "")
        if src_line:
            lines.append(f"- {filename}:{line_no}  →  {src_line.strip()}")
        else:
            lines.append(f"- {filename}:{line_no}")
    return "\n".join(lines) if lines else "No uncovered lines data available."


def _build_function_summary(function_coverage: list[dict]) -> str:
    """构建函数级覆盖率摘要。"""
    if not function_coverage:
        return "No function coverage data."

    lines = []
    zero = [f for f in function_coverage if f.get("line_cover") == "0.00%"]
    if zero:
        lines.append(f"Zero coverage ({len(zero)} functions):")
        for f in zero[:15]:
            lines.append(f"  - {f['function']} ({f.get('lines_count', '?')} lines)")

    low = [f for f in function_coverage if f.get("line_cover") not in ("0.00%", None)
           and float(f["line_cover"].rstrip("%")) < 50]
    if low:
        lines.append(f"\nLow coverage ({len(low)} functions):")
        for f in sorted(low, key=lambda x: float(x["line_cover"].rstrip("%")))[:10]:
            lines.append(f"  - {f['function']}: {f['line_cover']}")

    return "\n".join(lines) if lines else "All functions have good coverage."


def _parse_analyst_response(raw: str) -> dict:
    """解析 LLM 返回的 JSON 分析结果。"""
    import json
    import re

    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return {
        "uncovered_clusters": [],
        "constraints": [{"target": "general", "precondition": raw[:500]}],
        "knowledge_updates": {},
    }


def _update_slot_knowledge(existing: dict, analysis: dict, round_num: int) -> dict:
    """将 Analyst 发现合并到该 slot 的 SlotKnowledge 中（隔离）。"""
    updated = {
        "constraints_discovered": list(existing.get("constraints_discovered", [])),
        "positive_patterns": list(existing.get("positive_patterns", [])),
        "negative_patterns": list(existing.get("negative_patterns", [])),
    }

    knowledge_updates = analysis.get("knowledge_updates", {})

    for c in knowledge_updates.get("constraints_discovered", []):
        c["round"] = round_num
        updated["constraints_discovered"].append(c)

    for p in knowledge_updates.get("positive_patterns", []):
        p["round"] = round_num
        updated["positive_patterns"].append(p)

    for n in knowledge_updates.get("negative_patterns", []):
        n["round"] = round_num
        updated["negative_patterns"].append(n)

    return updated
