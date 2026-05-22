"""Planner Agent：基于 LLM 场景推理的 harness slot 分配与迭代管理。

本 agent 是 pipeline 的"大脑"，决定每一轮用什么策略生成 fuzz driver。

Round 1（LLM 场景推理）：
  - 将完整的 API 知识（签名、调用图、类型定义）提供给 LLM
  - LLM 分析 API 语义关系，设计独立的 fuzz 测试场景
  - 每个场景对应一个 HarnessSlot，包含 primary/setup/teardown API 和匹配策略
  - 如果 LLM 调用失败，降级到规则引擎 fallback

Round 2+（迭代改进）：
  - 所有 active slot 独立改进
  - 从该 slot 自己的 Analyst 输出中提取 target_apis
  - 单个 slot 连续 2 轮无提升 → 切换到 targeted-expansion
  - 单个 slot plateau_count >= 4 → 标记为 converged

策略库位于 strategies/*.md，每个文件有 YAML frontmatter 描述元数据。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.knowledge.context_builder import build_planner_context
from src.knowledge.grouping import group_apis, match_strategy
from src.pipeline.state import HarnessSlot, PipelineState, StrategySelection

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"
PLANNER_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "planner_prompt.txt").read_text(
    encoding="utf-8"
)

# PLACEHOLDER_PLANNER_REST


def _load_strategy_metadata() -> list[dict]:
    """加载所有策略文件的 YAML frontmatter 元数据。"""
    strategies = []
    for f in sorted(STRATEGIES_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        content = f.read_text(encoding="utf-8")
        meta = _parse_frontmatter(content)
        if meta and meta.get("id"):
            strategies.append(meta)
    return strategies


def _parse_frontmatter(content: str) -> dict | None:
    """解析策略文件的 YAML frontmatter。"""
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end < 0:
        return None
    import yaml
    try:
        return yaml.safe_load(content[3:end])
    except Exception:
        meta = {}
        for line in content[3:end].strip().splitlines():
            if ":" in line and not line.startswith(" "):
                key, val = line.split(":", 1)
                val = val.strip().strip('"')
                if val.startswith("["):
                    try:
                        meta[key.strip()] = json.loads(val.replace("'", '"'))
                    except Exception:
                        meta[key.strip()] = val
                else:
                    meta[key.strip()] = val
        return meta


def _format_strategies(strategy_metadata: list[dict]) -> str:
    """格式化策略列表供 prompt 使用。"""
    lines = []
    for meta in strategy_metadata:
        best_for = ", ".join(meta.get("best_for", []))
        lines.append(f"- **{meta['id']}**: {meta.get('name', '')} — best for: {best_for}")
    return "\n".join(lines)


def planner_node(state: PipelineState) -> dict:
    """LangGraph 节点：Round 1 LLM 场景推理，Round 2+ 各 slot 独立迭代。"""
    round_num = state.get("round", 0)
    knowledge = state.get("knowledge", {})
    slots = list(state.get("harness_slots", []))

    if round_num == 0:
        return _plan_initial(state, knowledge)
    else:
        return _plan_iterate(state, slots)


def _plan_initial(state: PipelineState, knowledge: dict) -> dict:
    """Round 1：LLM 场景推理 → 创建 harness slots。"""
    target_config = state["target_config"]
    lib_name = target_config.get("library_name", "?")
    strategy_metadata = _load_strategy_metadata()

    context = build_planner_context(knowledge, [], strategy_metadata)
    strategies_text = _format_strategies(strategy_metadata)

    prompt = PLANNER_TEMPLATE.replace("{{LIBRARY_NAME}}", lib_name)
    prompt = prompt.replace("{{KNOWLEDGE_CONTEXT}}", context)
    prompt = prompt.replace("{{CALL_GRAPH}}", _format_call_graph(knowledge))
    prompt = prompt.replace("{{TYPE_DEFINITIONS}}", _format_type_defs(knowledge))
    prompt = prompt.replace("{{STRATEGIES}}", strategies_text)

    try:
        llm = create_llm(temperature=max(state.get("temperature", 0.4) - 0.1, 0.1))
        response = llm.invoke([
            SystemMessage(content="You are an expert fuzz testing engineer specializing in C/C++ library security testing."),
            HumanMessage(content=prompt),
        ])
        prompt_tok, completion_tok = extract_token_usage(response)
        get_tracker().record("planner", "default", prompt_tok, completion_tok)

        raw = response.content if isinstance(response.content, str) else str(response.content)
        slot_specs = _parse_planner_output(raw, knowledge)

        if not slot_specs:
            raise ValueError("LLM returned no valid slots")

        slots, strategy_selections = _build_slots_from_specs(slot_specs, strategy_metadata)
        print(f"[Planner] Round 1 — {lib_name}: LLM assigned {len(slots)} harness slots", flush=True)

    except Exception as e:
        print(f"[Planner] LLM failed ({e}), falling back to rule engine", flush=True)
        slots, strategy_selections = _fallback_rule_engine(knowledge, strategy_metadata)
        print(f"[Planner] Round 1 — {lib_name}: fallback assigned {len(slots)} harness slots", flush=True)

    for slot in slots:
        print(f"[Planner]   {slot['slot_id']}: strategy={slot['strategy_history'][0]}, "
              f"apis={len(slot['target_apis'])}", flush=True)

    messages = state.get("messages", []) + [
        f"[Planner] Round 1: {len(slots)} slots assigned"
    ]

    return {
        "harness_slots": slots,
        "strategy_selections": strategy_selections,
        "messages": messages,
    }

# PLACEHOLDER_PLANNER_HELPERS


def _format_call_graph(knowledge: dict) -> str:
    """格式化调用图信息。"""
    call_graph = knowledge.get("call_graph", {})
    if not call_graph:
        return "No call graph data available."
    api_names = {a["name"] for a in knowledge.get("api_entries", [])}
    lines = []
    for caller, callees in sorted(call_graph.items()):
        if caller in api_names and callees:
            visible = [c for c in callees if c in api_names]
            if visible:
                lines.append(f"- {caller} → {', '.join(visible)}")
    return "\n".join(lines) if lines else "No inter-API calls detected."


def _format_type_defs(knowledge: dict) -> str:
    """格式化类型定义摘要。"""
    type_defs = knowledge.get("type_definitions", [])
    if not type_defs:
        return "No type definitions available."
    lines = []
    for td in type_defs[:25]:
        kind = td.get("kind", "struct")
        name = td.get("name", "?")
        members = td.get("members", [])
        if members:
            member_str = ", ".join(f"{m.get('type', '?')} {m.get('name', '?')}" for m in members[:6])
            lines.append(f"- {kind} {name} {{ {member_str} }}")
        else:
            lines.append(f"- {kind} {name}")
    return "\n".join(lines)


def _parse_planner_output(raw: str, knowledge: dict) -> list[dict]:
    """解析 LLM 输出的 JSON，校验 API 名称有效性。"""
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*\n(.+?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start >= 0:
            text = text[start:]

    data = json.loads(text)
    slots_raw = data.get("slots", [])
    if not slots_raw:
        return []

    valid_apis = {a["name"] for a in knowledge.get("api_entries", [])}
    valid_strategies = {
        "parse-centric", "multi-api-sequence", "roundtrip", "targeted-expansion",
        "structure-aware", "error-path", "stateful", "resource-boundary",
        "callback-driven", "differential",
    }

    result = []
    for spec in slots_raw:
        primary = [a for a in spec.get("primary_apis", []) if a in valid_apis]
        if not primary:
            continue
        setup = [a for a in spec.get("setup_apis", []) if a in valid_apis]
        teardown = [a for a in spec.get("teardown_apis", []) if a in valid_apis]
        strategy = spec.get("strategy_id", "multi-api-sequence")
        if strategy not in valid_strategies:
            strategy = "multi-api-sequence"

        result.append({
            "slot_id": spec.get("slot_id", f"slot_{len(result)}"),
            "description": spec.get("description", ""),
            "primary_apis": primary,
            "setup_apis": setup,
            "teardown_apis": teardown,
            "strategy_id": strategy,
            "rationale": spec.get("rationale", ""),
        })

    return result


def _build_slots_from_specs(
    specs: list[dict], strategy_metadata: list[dict]
) -> tuple[list[HarnessSlot], list[StrategySelection]]:
    """从 LLM 输出的 slot specs 创建 HarnessSlot 和 StrategySelection。"""
    slots: list[HarnessSlot] = []
    selections: list[StrategySelection] = []

    for spec in specs:
        slot_id = spec["slot_id"]
        all_apis = list(set(spec["primary_apis"] + spec["setup_apis"] + spec["teardown_apis"]))

        slots.append(HarnessSlot(
            slot_id=slot_id,
            group_id=slot_id,
            description=spec.get("description", ""),
            target_apis=all_apis,
            primary_apis=spec["primary_apis"],
            setup_apis=spec["setup_apis"],
            teardown_apis=spec["teardown_apis"],
            current_source="",
            best_source="",
            best_coverage=0.0,
            best_branch_coverage=0.0,
            best_uncovered_lines=[],
            best_function_coverage=[],
            strategy_history=[spec["strategy_id"]],
            coverage_history=[],
            status="active",
            plateau_count=0,
        ))
        selections.append(StrategySelection(
            slot_id=slot_id,
            strategy_id=spec["strategy_id"],
            rationale=spec.get("rationale", ""),
            target_apis=all_apis,
        ))

    return slots, selections


def _fallback_rule_engine(
    knowledge: dict, strategy_metadata: list[dict]
) -> tuple[list[HarnessSlot], list[StrategySelection]]:
    """LLM 失败时降级到旧的规则引擎。"""
    groups = group_apis(knowledge, max_groups=10)
    slots: list[HarnessSlot] = []
    selections: list[StrategySelection] = []

    for i, group in enumerate(groups):
        strategy_id = match_strategy(group, strategy_metadata)
        slot_id = f"slot_{i}"

        slots.append(HarnessSlot(
            slot_id=slot_id,
            group_id=group["group_id"],
            description=f"Fallback group: {group['group_id']}",
            target_apis=group["apis"],
            primary_apis=group["apis"],
            setup_apis=[],
            teardown_apis=[],
            current_source="",
            best_source="",
            best_coverage=0.0,
            best_branch_coverage=0.0,
            best_uncovered_lines=[],
            best_function_coverage=[],
            strategy_history=[strategy_id],
            coverage_history=[],
            status="active",
            plateau_count=0,
        ))
        selections.append(StrategySelection(
            slot_id=slot_id,
            strategy_id=strategy_id,
            rationale=f"Fallback: group '{group['group_id']}' features={group['features']}",
            target_apis=group["apis"],
        ))

    return slots, selections

# PLACEHOLDER_ITERATE


def _plan_iterate(state: PipelineState, slots: list[HarnessSlot]) -> dict:
    """Round 2+：每个 active slot 独立改进，plateau 时切换策略。"""
    round_num = state.get("round", 0)
    slot_analyses = state.get("slot_coverage_analyses", {})
    active_count = sum(1 for s in slots if s["status"] == "active")
    print(f"[Planner] Round {round_num + 1} — iterating {active_count} active slots", flush=True)

    updated_slots = []
    strategy_selections: list[StrategySelection] = []

    for slot in slots:
        if slot["status"] != "active":
            updated_slots.append(slot)
            continue

        if slot.get("plateau_count", 0) >= 4:
            updated_slots.append({**slot, "status": "converged"})
            print(f"[Planner]   {slot['slot_id']} converged (plateau={slot['plateau_count']})", flush=True)
            continue

        updated_slots.append(slot)

        if slot.get("plateau_count", 0) >= 2:
            strategy_id = "targeted-expansion"
        else:
            strategy_id = slot["strategy_history"][-1]

        slot_analysis = slot_analyses.get(slot["slot_id"], {})
        target_apis = _extract_slot_targets(slot, slot_analysis)

        strategy_selections.append(StrategySelection(
            slot_id=slot["slot_id"],
            strategy_id=strategy_id,
            rationale=f"Iterate: {'targeted-expansion (plateau)' if strategy_id == 'targeted-expansion' else 'continue'}",
            target_apis=target_apis,
        ))
        print(f"[Planner]   {slot['slot_id']}: strategy={strategy_id}, "
              f"plateau={slot.get('plateau_count', 0)}", flush=True)

    messages = state.get("messages", []) + [
        f"[Planner] Round {round_num + 1}: {len(strategy_selections)} slots iterating"
    ]

    return {
        "harness_slots": updated_slots,
        "strategy_selections": strategy_selections,
        "messages": messages,
    }


def _extract_slot_targets(slot: HarnessSlot, slot_analysis: dict) -> list[str]:
    """从 slot 的 analyst 输出中提取 target_apis。"""
    target_apis = list(slot.get("target_apis", []))

    if slot_analysis:
        clusters = slot_analysis.get("uncovered_clusters", [])
        extra = []
        for cluster in clusters:
            extra.extend(cluster.get("functions", []))
        if extra:
            seen = set(target_apis)
            target_apis.extend(f for f in extra if f not in seen)

    return target_apis
