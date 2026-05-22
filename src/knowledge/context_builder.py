"""上下文构建器：为每个 agent 组装所需的知识子集。

本文件是 KnowledgeStore 和 LLM prompt 之间的翻译层。
每个 agent 需要的知识子集不同，负责从完整的 KnowledgeStore 中
挑选相关信息，格式化为 markdown 字符串，拼入 LLM prompt。

三个消费者，三种上下文：
┌─────────────────────────────────────────────────────────────────┐
│ build_planner_context  → Planner                                │
│   提供：API 分类摘要 + slot 覆盖率历史 + 可用策略列表           │
│   目的：帮助 Planner 选择最适合的策略                           │
│                                                                 │
│ build_generator_context → Generator                             │
│   提供：目标 API 详情（含 ownership）+ 类型定义 + 宏常量        │
│         + 约束 + 正/负模式                                      │
│   目的：让 LLM 生成正确的 fuzz driver 代码                      │
│                                                                 │
│ build_analyst_context → Analyst                                 │
│   提供：完整 API 列表 + 调用图 + 未覆盖源码行                   │
│   目的：帮助 Analyst 诊断覆盖率缺口的根因                       │
└─────────────────────────────────────────────────────────────────┘

设计原则：
- 全量传递静态知识（API、类型、宏），不做截断
- 当 Planner 指定 target_apis 时，Generator 上下文聚焦到目标 API 子集
- 信息按 category 分组、有文档注释的优先排列
- extractor 提取的所有字段都必须在某个 context 中被使用
"""

from __future__ import annotations

from src.pipeline.state import HarnessSlot, KnowledgeStore, StrategySelection


def build_planner_context(
    knowledge: KnowledgeStore,
    slots: list[HarnessSlot],
    strategy_metadata: list[dict],
) -> str:
    """为 Planner agent 构建上下文。

    Round 1（slots 为空）：提供完整 API 知识供 LLM 做场景推理。
    Round 2+（slots 非空）：额外提供 slot 状态供迭代决策。
    """
    lines = ["## Target Library API Surface\n"]

    # 按 category 分组展示 API（含参数详情）
    by_category: dict[str, list] = {}
    for api in knowledge["api_entries"]:
        by_category.setdefault(api["category"], []).append(api)

    for cat in ["parse", "create", "modify", "query", "delete", "serialize", "utility"]:
        apis = by_category.get(cat, [])
        if apis:
            lines.append(f"### {cat.title()} ({len(apis)} functions)\n")
            for api in apis:
                params_str = ", ".join(
                    f"{p['type']} {p['name']}" for p in api.get("params", [])
                )
                lines.append(f"- `{api['return_type']} {api['name']}({params_str})`")
            lines.append("")

    # 调用图（展示 API 间的调用关系）
    call_graph = knowledge.get("call_graph", {})
    if call_graph:
        lines.append("## Call Graph (caller → callees)\n")
        api_names = {a["name"] for a in knowledge["api_entries"]}
        for caller, callees in sorted(call_graph.items()):
            if caller in api_names and callees:
                visible_callees = [c for c in callees if c in api_names]
                if visible_callees:
                    lines.append(f"- `{caller}` → {', '.join(f'`{c}`' for c in visible_callees)}")
        lines.append("")

    # 类型定义摘要（struct/enum 成员）
    type_defs = knowledge.get("type_definitions", [])
    if type_defs:
        lines.append("## Key Type Definitions\n")
        for td in type_defs[:20]:
            kind = td.get("kind", "struct")
            name = td.get("name", "?")
            members = td.get("members", [])
            if members:
                member_str = ", ".join(m.get("name", "?") for m in members[:8])
                lines.append(f"- `{kind} {name}` {{ {member_str} }}")
            else:
                lines.append(f"- `{kind} {name}`")
        lines.append("")

    # 每个 slot 的当前状态（Round 2+ 使用）
    if slots:
        lines.append("## Current Harness Slots\n")
        for slot in slots:
            status = slot["status"]
            history = slot["coverage_history"]
            strat = slot["strategy_history"][-1] if slot["strategy_history"] else "none"
            lines.append(f"- **{slot['slot_id']}** [{status}]: strategy={strat}, coverage={history}")
        lines.append("")

    return "\n".join(lines)


def build_generator_context(
    knowledge: KnowledgeStore,
    selection: StrategySelection,
    slot: HarnessSlot | None = None,
    slot_knowledge: dict | None = None,
) -> str:
    """为 Generator agent 构建上下文。

    Generator 需要：
    1. 目标 API 的完整信息（签名、参数类型、ownership、preconditions）
    2. 类型定义（struct 字段、enum 值，用于构造合法输入）
    3. 宏常量（类型标志、限制值，用于生成合法的枚举参数）
    4. 累积的约束和模式（从该 slot 自己的 SlotKnowledge 中获取，隔离）
    """
    lines = ["## API Knowledge\n"]

    # 确定目标 API 范围：Planner 指定 target_apis 时聚焦，否则全量传递
    target_api_names = set(selection.get("target_apis", []))
    relevant_apis = list(knowledge["api_entries"])
    if target_api_names:
        relevant_apis = [a for a in knowledge["api_entries"] if a["name"] in target_api_names]
        for api_name in list(target_api_names):
            callees = knowledge["call_graph"].get(api_name, [])
            for callee in callees:
                for a in knowledge["api_entries"]:
                    if a["name"] == callee and a not in relevant_apis:
                        relevant_apis.append(a)

    # 按 category 分组，每组内有文档注释的排前面（帮助 LLM 优先关注信息丰富的 API）
    _CATEGORY_PRIORITY = ["parse", "create", "delete", "serialize", "modify", "query", "utility"]
    sorted_apis: list = []
    for cat in _CATEGORY_PRIORITY:
        cat_apis = [a for a in relevant_apis if a["category"] == cat]
        cat_apis.sort(key=lambda a: not bool(a.get("doc_comment")))
        sorted_apis.extend(cat_apis)
    # 追加未匹配任何已知 category 的 API
    known_cats = set(_CATEGORY_PRIORITY)
    sorted_apis.extend(a for a in relevant_apis if a["category"] not in known_cats)

    # 展示每个 API 的详细信息
    for api in sorted_apis:
        lines.append(f"### `{api['name']}`")
        lines.append(f"- Signature: `{api['signature']}`")
        lines.append(f"- Category: {api['category']}")
        if api.get("doc_comment"):
            lines.append(f"- Doc: {api['doc_comment']}")
        if api["preconditions"]:
            lines.append(f"- Preconditions: {'; '.join(api['preconditions'])}")
        if api["params"]:
            for p in api["params"]:
                nullable = " (nullable)" if p.get("nullable") else ""
                ownership = f" [{p['ownership']}]" if p.get("ownership", "borrow") != "borrow" else ""
                lines.append(f"  - `{p['name']}`: `{p['type']}`{nullable}{ownership}")
        lines.append("")

    # 类型定义（struct 字段、enum 值——帮助 Generator 构造合法输入）
    types = knowledge["type_definitions"]
    if types:
        lines.append("## Key Type Definitions\n")
        for t in types:
            if t["kind"] == "struct":
                fields_str = ", ".join(f"{f['name']}: {f['type']}" for f in t.get("fields", []))
                lines.append(f"- struct **{t['name']}** {{ {fields_str} }}")
            elif t["kind"] == "enum":
                vals = ", ".join(t.get("values", []))
                lines.append(f"- enum **{t['name']}** {{ {vals} }}")
            elif t["kind"] == "typedef":
                lines.append(f"- typedef **{t['name']}** = `{t.get('underlying', '')}`")
        lines.append("")

    # 宏常量（类型标志和限制值——Generator 需要知道合法的枚举值）
    macros = knowledge.get("macro_constants", [])
    if macros:
        # 按 kind 分组展示
        flags = [m for m in macros if m["kind"] == "flag"]
        constants = [m for m in macros if m["kind"] == "constant" and m["value"]]
        func_macros = [m for m in macros if m["kind"] == "function_like"]

        lines.append("## Macro Constants\n")
        if flags:
            lines.append("**Type flags** (use these as valid enum values):")
            for m in flags:
                lines.append(f"- `{m['name']}` = {m['value']}")
            lines.append("")
        if constants:
            lines.append("**Limits and constants:**")
            for m in constants:
                lines.append(f"- `{m['name']}` = {m['value']}")
            lines.append("")
        if func_macros:
            lines.append("**Utility macros:**")
            for m in func_macros:
                lines.append(f"- `{m['name']}`")
            lines.append("")

    # 累积的约束（从该 slot 的 SlotKnowledge 中获取，隔离不污染）
    sk = slot_knowledge or {}
    constraints = sk.get("constraints_discovered", [])
    if constraints:
        lines.append("## Known Constraints (from prior rounds)\n")
        for c in constraints:
            lines.append(f"- **{c.get('api', '?')}**: {c.get('constraint', '')}")
        lines.append("")

    # 正/负模式（该 slot 跨轮次积累的经验——什么有效、什么失败）
    pos = sk.get("positive_patterns", [])
    neg = sk.get("negative_patterns", [])
    if pos:
        lines.append("## Patterns That Worked\n")
        for p in pos:
            lines.append(f"- {p.get('pattern', '')} (coverage gain: +{p.get('coverage_gain', 0):.1f}%)")
        lines.append("")
    if neg:
        lines.append("## Patterns That Failed\n")
        for n in neg:
            lines.append(f"- {n.get('pattern', '')}: {n.get('failure_reason', '')}")
        lines.append("")

    return "\n".join(lines)


def build_analyst_context(
    knowledge: KnowledgeStore,
    uncovered_source: dict[str, dict[int, str]] | None = None,
) -> str:
    """为 Analyst agent 构建上下文。

    Analyst 需要：
    1. 完整 API 列表（了解库的全部接口，判断哪些还没覆盖）
    2. 调用图（理解函数间依赖，判断覆盖某函数需要先调用什么）
    3. 宏常量（理解类型标志含义，判断分支条件）
    4. 未覆盖的源码行（诊断覆盖率缺口的具体位置和原因）
    """
    lines = ["## Full API Inventory\n"]

    # 完整 API 列表（签名 + 分类 + ownership 标记）
    for api in knowledge["api_entries"]:
        ownership_mark = ""
        if any(p.get("ownership") == "transfer" for p in api.get("params", [])):
            ownership_mark = " [takes ownership]"
        if api.get("preconditions"):
            ownership_mark += " ⚠️"
        lines.append(f"- `{api['signature']}` [{api['category']}]{ownership_mark}")
    lines.append("")

    # 调用图（展示所有 caller，帮助 Analyst 理解函数间依赖链）
    if knowledge["call_graph"]:
        lines.append("## Call Graph\n")
        for caller, callees in sorted(knowledge["call_graph"].items()):
            lines.append(f"- {caller} → {', '.join(callees[:8])}")
        lines.append("")

    # 宏常量摘要（帮助 Analyst 理解分支条件中的常量含义）
    macros = knowledge.get("macro_constants", [])
    flags = [m for m in macros if m["kind"] == "flag"]
    if flags:
        lines.append("## Type Flags (used in switch/if conditions)\n")
        for m in flags:
            lines.append(f"- `{m['name']}` = {m['value']}")
        lines.append("")

    # 未覆盖源码行（Analyst 诊断的核心输入）
    if uncovered_source:
        lines.append("## Uncovered Source Lines\n")
        for filename, line_map in list(uncovered_source.items())[:5]:
            lines.append(f"### {filename}")
            for line_no, code in sorted(line_map.items())[:20]:
                lines.append(f"  {line_no}: {code.rstrip()}")
            lines.append("")

    return "\n".join(lines)

