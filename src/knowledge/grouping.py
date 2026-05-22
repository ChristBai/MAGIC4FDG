"""API 分组与策略匹配：将目标库 API 按共享上下文类型聚类，并为每组匹配最佳策略。

这是多入口 driver 分配机制的核心模块。工作流程：
1. 从 KnowledgeStore 中筛选适合 fuzz 的 API（排除 getter/内部函数）
2. 按第一个指针参数类型聚类为 API Group
3. 调整组大小（合并过小组、拆分过大组）
4. 为每个组提取特征标签（has_parser, has_lifecycle 等）
5. 用规则引擎将特征匹配到最佳策略（不调 LLM）

设计原则：
- 所有库统一走此流程（不区分大小库）
- 策略匹配是确定性的规则引擎，不依赖 LLM
- 每个 group 恰好匹配一个策略，一对一分配到 slot
"""

from __future__ import annotations

import re
from collections import defaultdict

from src.pipeline.state import APIGroup, KnowledgeStore


# =============================================================================
# 策略特征映射（硬编码：策略 ID → 需要的特征集合）
# =============================================================================

STRATEGY_FEATURES: dict[str, list[str]] = {
    "parse-centric": ["has_parser", "has_buffer_params"],
    "multi-api-sequence": ["has_lifecycle", "has_state_context"],
    "roundtrip": ["has_parser", "has_serializer"],
    "stateful": ["has_state_context", "has_lifecycle"],
    "callback-driven": ["has_callbacks"],
    "structure-aware": ["has_parser", "has_buffer_params"],
    "error-path": ["has_error_codes"],
    "resource-boundary": ["has_buffer_params"],
    "differential": ["has_multiple_variants"],
}

# 策略优先级（平局时使用，越小越优先）
STRATEGY_PRIORITY: dict[str, int] = {
    "roundtrip": 1,
    "parse-centric": 2,
    "stateful": 3,
    "multi-api-sequence": 4,
    "structure-aware": 5,
    "callback-driven": 6,
    "error-path": 7,
    "resource-boundary": 8,
    "differential": 9,
}

_BUFFER_TYPES = {"uint8_t *", "const uint8_t *", "char *", "const char *",
                 "void *", "const void *", "unsigned char *", "const unsigned char *"}

_INTERNAL_PREFIXES = ("_", "__", "internal_")


# =============================================================================
# 公开接口
# =============================================================================

def group_apis(knowledge: KnowledgeStore, max_groups: int = 10) -> list[APIGroup]:
    """将 API 按共享上下文类型聚类为 fuzzing groups。"""
    api_entries = knowledge["api_entries"]
    call_graph = knowledge.get("call_graph", {})

    fuzzable = [api for api in api_entries if _is_fuzzable(api)]
    if not fuzzable:
        fuzzable = api_entries[:10]
 
    groups_by_ctx = _cluster_by_context_type(fuzzable) 
    groups_by_ctx = _merge_small_groups(groups_by_ctx)
    groups_by_ctx = _split_large_groups(groups_by_ctx)

    api_groups: list[APIGroup] = []
    for ctx_type, apis in groups_by_ctx.items():
        group_id = _make_group_id(ctx_type, apis)
        features = _extract_features(apis, call_graph)
        api_groups.append(APIGroup(
            group_id=group_id,
            apis=[a["name"] for a in apis],
            context_type=ctx_type,
            features=features,
        ))

    api_groups.sort(key=lambda g: _priority_score(g), reverse=True)
    return api_groups[:max_groups]


def match_strategy(group: APIGroup, strategy_metadata: list[dict] | None = None) -> str:
    """为一个 API group 匹配最佳策略（规则引擎，不调 LLM）。

    评分规则：
    1. 绝对匹配数优先（匹配更多特征的策略更好）
    2. 匹配数相同时，匹配比例高的优先（完全匹配 > 部分匹配）
    3. 仍然平局时，按策略优先级排序
    """
    group_features = set(group["features"])
    best_strategy = "multi-api-sequence"
    best_matched = -1
    best_ratio = -1.0
    best_priority = 999

    for strategy_id, required_features in STRATEGY_FEATURES.items():
        if strategy_id == "targeted-expansion":
            continue
        if not required_features:
            continue
        matched = sum(1 for f in required_features if f in group_features)
        if matched == 0:
            continue
        ratio = matched / len(required_features)
        priority = STRATEGY_PRIORITY.get(strategy_id, 99)

        better = (
            matched > best_matched
            or (matched == best_matched and ratio > best_ratio)
            or (matched == best_matched and ratio == best_ratio and priority < best_priority)
        )
        if better:
            best_matched = matched
            best_ratio = ratio
            best_strategy = strategy_id
            best_priority = priority

    return best_strategy


# =============================================================================
# 筛选逻辑
# =============================================================================

def _is_fuzzable(api: dict) -> bool:
    """判断一个 API 是否适合作为 fuzz 入口。"""
    name = api.get("name", "")
    if any(name.lower().startswith(p) for p in _INTERNAL_PREFIXES):
        return False
    if "deprecated" in api.get("doc_comment", "").lower():
        return False

    params = api.get("params", [])
    if len(params) == 0:
        return False
    if len(params) == 1 and not _has_pointer_param(params):
        return False

    return True


def _has_pointer_param(params: list[dict]) -> bool:
    return any("*" in p.get("type", "") for p in params)


# =============================================================================
# 聚类逻辑
# =============================================================================

def _get_context_type(api: dict) -> str:
    """提取 API 的上下文类型（第一个指针参数的类型）。"""
    for param in api.get("params", []):
        ptype = param.get("type", "")
        if "*" in ptype and ptype not in _BUFFER_TYPES:
            return ptype.replace("const ", "").strip()
    category = api.get("category", "misc")
    return f"_category_{category}"


def _cluster_by_context_type(apis: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for api in apis:
        ctx = _get_context_type(api)
        groups[ctx].append(api)
    return dict(groups)


def _merge_small_groups(groups: dict[str, list[dict]], min_size: int = 2) -> dict[str, list[dict]]:
    """合并过小的组到 _misc 组。"""
    merged: dict[str, list[dict]] = {}
    misc: list[dict] = []
    for ctx, apis in groups.items():
        if len(apis) < min_size:
            misc.extend(apis)
        else:
            merged[ctx] = apis
    if misc:
        merged["_misc"] = misc
    return merged


def _split_large_groups(groups: dict[str, list[dict]], max_size: int = 10) -> dict[str, list[dict]]:
    """拆分过大的组（按 category 子分）。"""
    result: dict[str, list[dict]] = {}
    for ctx, apis in groups.items():
        if len(apis) <= max_size:
            result[ctx] = apis
            continue
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for api in apis:
            by_cat[api.get("category", "misc")].append(api)
        for cat, cat_apis in by_cat.items():
            result[f"{ctx}_{cat}"] = cat_apis
    return result


# =============================================================================
# 特征提取
# =============================================================================

def _extract_features(apis: list[dict], call_graph: dict[str, list[str]]) -> list[str]:
    """从一组 API 中提取特征标签，用于策略匹配。"""
    features: list[str] = []
    categories = {api.get("category", "") for api in apis}
    all_params = [p for api in apis for p in api.get("params", [])]
    all_param_types = {p.get("type", "") for p in all_params}
    all_return_types = {api.get("return_type", "") for api in apis}

    if "parse" in categories:
        features.append("has_parser")
    if "serialize" in categories:
        features.append("has_serializer")
    if ("create" in categories and "delete" in categories) or \
       ("delete" in categories and "modify" in categories):
        features.append("has_lifecycle")
    if any("(*)" in t or "(*" in t for t in all_param_types):
        features.append("has_callbacks")
    if any(t in _BUFFER_TYPES for t in all_param_types):
        features.append("has_buffer_params")
    if any("size_t" in t for t in all_param_types):
        features.append("has_buffer_params")

    # has_state_context: 第一个参数是非 buffer 指针类型
    ctx_types = set()
    for api in apis:
        params = api.get("params", [])
        if params:
            ptype = params[0].get("type", "")
            if "*" in ptype and ptype not in _BUFFER_TYPES:
                ctx_types.add(ptype)
    if ctx_types:
        features.append("has_state_context")

    # has_error_codes: 返回 int 且函数名含 error/status/result
    for api in apis:
        rt = api.get("return_type", "")
        name = api.get("name", "").lower()
        if rt in ("int", "int32_t") and any(k in name for k in ("error", "status", "result", "ret")):
            features.append("has_error_codes")
            break

    # has_multiple_variants: 同前缀不同后缀的函数 ≥ 3
    if _has_multiple_variants(apis):
        features.append("has_multiple_variants")

    return list(set(features))


def _has_multiple_variants(apis: list[dict]) -> bool:
    """检查是否有同前缀不同后缀的函数 ≥ 3 个。"""
    names = [api.get("name", "") for api in apis]
    prefix_counts: dict[str, int] = defaultdict(int)
    for name in names:
        parts = name.rsplit("_", 1)
        if len(parts) == 2:
            prefix_counts[parts[0]] += 1
    return any(count >= 3 for count in prefix_counts.values())


# =============================================================================
# 辅助函数
# =============================================================================

def _make_group_id(ctx_type: str, apis: list[dict]) -> str:
    """生成可读的 group ID。"""
    if ctx_type.startswith("_category_"):
        return ctx_type.replace("_category_", "")
    if ctx_type == "_misc":
        return "misc"
    clean = re.sub(r"[^a-zA-Z0-9_]", "", ctx_type.replace(" ", "_").replace("*", ""))
    return clean.lower()[:30] or "unknown"


def _priority_score(group: APIGroup) -> float:
    """计算组优先级分数（越高越优先）。"""
    n_apis = len(group["apis"])
    n_features = len(group["features"])
    return n_apis * (1 + 0.5 * n_features)
