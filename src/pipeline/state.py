"""流水线状态定义：MAGIC4FDG v2 的数据字典。

所有 agent 之间通过一个共享的 state 字典通信，本文件定义 state 中
每个字段的类型和含义。

核心概念：
- LangGraph 是图执行框架，每个节点（agent）接收 state、返回部分更新
- TypedDict 是 Python 类型标注工具，让 IDE 能提示字段名和类型
- 这些类型不会在运行时强制检查，但能帮助开发者理解数据结构

数据流向：
  PipelineState 是最外层容器，包含所有子结构
  ┌─────────────────────────────────────────────────┐
  │ PipelineState                                    │
  │  ├── knowledge: KnowledgeStore (知识库)          │
  │  │    └── api_entries: list[APIEntry]            │
  │  ├── strategy_selections: list[StrategySelection]│
  │  ├── harness_slots: list[HarnessSlot]           │
  │  ├── variants: list[DriverVariant]              │
  │  │    └── config: VariantConfig                 │
  │  └── coverage_analysis: dict (Analyst 输出)      │
  └─────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Literal, TypedDict


# =============================================================================
# 第一层：知识库相关的数据结构
# =============================================================================

class APIEntry(TypedDict):
    """目标库中的一个 API 函数。

    由 Knowledge Agent 通过 clang AST 解析自动提取，不需要 LLM。
    """

    name: str           # 函数名，如 "cJSON_Parse"
    signature: str      # 完整函数签名，如 "cJSON *(const char *)"
    return_type: str    # 返回类型，如 "cJSON *"
    params: list[dict]  # 参数列表，每个参数是 {name, type, nullable, ownership}
    category: str       # API 分类：parse/create/modify/query/delete/serialize/utility
    preconditions: list[str]  # 调用前置条件（由 Analyst 在后续轮次中填充）
    group: str          # 功能簇名称，用于将相关 API 分组
    doc_comment: str    # 头文件中的原始文档注释（全量保留，供 LLM 理解语义）


class SlotKnowledge(TypedDict):
    """单个 slot 的动态知识（per-slot 隔离，避免多 slot 间信息污染）。

    每个 slot 独立积累自己的约束和模式，Analyst 按 slot 分别输出。
    """

    constraints_discovered: list[dict]  # 该 slot 发现的 API 调用约束
    positive_patterns: list[dict]       # 该 slot 有效的代码模式
    negative_patterns: list[dict]       # 该 slot 失败的代码模式


class KnowledgeStore(TypedDict):
    """目标库的结构化知识库。

    分为两部分：
    1. 静态知识（Pipeline 启动时一次性提取，不再变化，所有 slot 共享只读）：
       - api_entries, call_graph, type_definitions, macro_constants
    2. 动态知识（per-slot 隔离，每轮由 Analyst 按 slot 分别追加）：
       - slot_knowledge: slot_id → SlotKnowledge
    """

    api_entries: list[APIEntry]             # 所有公开 API 函数列表
    call_graph: dict[str, list[str]]        # 调用图：函数名 -> [它调用的函数名列表]
    type_definitions: list[dict]            # 类型定义：struct/enum/typedef
    macro_constants: list[dict]             # 宏常量：{name, value, type}
    slot_knowledge: dict[str, SlotKnowledge]  # per-slot 动态知识（隔离）


# =============================================================================
# 第二层：策略规划相关的数据结构
# =============================================================================

class StrategySelection(TypedDict):
    """Planner 为一个 harness slot 分配的策略。"""

    slot_id: str            # 对应的 harness slot ID，如 "slot_0"
    strategy_id: str        # 策略 ID，对应 strategies/ 目录下的 .md 文件名
    rationale: str          # LLM 给出的选择理由
    target_apis: list[str]  # 该策略重点关注的 API 列表


class APIGroup(TypedDict):
    """一个 API 分组，代表一组共享上下文类型的相关 API。

    由 grouping.py 自动生成，每个 group 对应一个 HarnessSlot。
    """

    group_id: str           # 唯一标识，如 "evp_md_ctx", "cjson_parse"
    apis: list[str]         # 组内 API 名称列表
    context_type: str       # 共享的上下文类型（聚类主键）
    features: list[str]     # 组特征标签（用于策略匹配）


class HarnessSlot(TypedDict):
    """一个持久化的 harness 演化轨迹，跨轮次保持状态。

    每个 slot 对应一个独立的 fuzz 测试场景，由 Planner LLM 推理分配。
    核心概念：best_source 只在覆盖率提升时更新，保证不会丢失最佳成果。
    生命周期：active（仍在改进）→ converged（plateau 后停止）
    """

    slot_id: str                    # 唯一标识，如 "parse_json_input"
    group_id: str                   # 兼容字段（等同于 slot_id）
    description: str                # 场景描述（LLM 生成）
    target_apis: list[str]          # 该 slot 负责的 API 子集（primary + setup + teardown）
    primary_apis: list[str]         # 核心测试目标 API
    setup_apis: list[str]           # 前置依赖 API（创建状态）
    teardown_apis: list[str]        # 清理 API（释放资源）
    current_source: str             # 当前轮次产出的代码
    best_source: str                # 历史最佳代码（只升不降）
    best_coverage: float            # 历史最佳行覆盖率
    best_branch_coverage: float     # 历史最佳分支覆盖率
    best_uncovered_lines: list[dict]      # 最佳时的未覆盖行（供 Analyst 用）
    best_function_coverage: list[dict]    # 最佳时的函数覆盖率
    strategy_history: list[str]     # 使用过的策略列表
    coverage_history: list[float]   # 每轮覆盖率记录
    status: Literal["active", "converged"]
    plateau_count: int              # 连续未提升轮次数


# =============================================================================
# 第三层：代码生成相关的数据结构
# =============================================================================

class VariantConfig(TypedDict):
    """一个 driver 变体的生成配置（用于追溯来源）。"""

    model: str              # LLM 模型名，如 "claude-opus-4-6"
    prompt_strategy: str    # 策略 ID，如 "roundtrip"
    temperature: float      # LLM 采样温度，如 0.4


class DriverVariant(TypedDict):
    """一个 fuzz driver 变体，包含从生成到评估的完整生命周期数据。

    生命周期：Generation(pending) → Patching(ok/failed) → Coverage(填充覆盖率)
    注意：variants 列表每轮被替换，跨轮次持久数据在 HarnessSlot 中。
    """

    id: str                         # 唯一标识，如 "slot_1_roundtrip_r0"
    slot_id: str                    # 所属的 harness slot
    config: VariantConfig           # 生成配置
    source_code: str                # C/C++ 源代码
    compile_status: Literal["pending", "ok", "failed"]
    compile_errors: str             # 编译错误信息
    patch_attempts: int             # 编译修复尝试次数
    coverage_pct: float             # 行覆盖率（0-100）
    branch_coverage_pct: float      # 分支覆盖率（0-100）
    uncovered_lines: list[dict]     # 未覆盖行 [{file, line_no, reachable}]
    covered_lines: list[dict]       # 已覆盖行
    function_coverage: list[dict]   # 函数级覆盖率
    is_incremental: bool            # 是否为增量改进模式（Round 2+）


# =============================================================================
# 最外层：Pipeline 完整状态
# =============================================================================

class PipelineState(TypedDict):
    """LangGraph Pipeline 的完整状态，所有 agent 共享。

    每个 agent 接收完整 state，返回 dict 包含要更新的字段。
    LangGraph 自动合并（list 字段是替换，非追加）。
    """

    # === 输入配置 ===
    target_config: dict         # 目标库配置（从 targets/*.json 加载）
    target_config_path: str     # 配置文件路径

    # === 知识库 ===
    knowledge: KnowledgeStore   # 结构化知识（静态 + 动态累积）

    # === 策略规划 ===
    strategy_selections: list[StrategySelection]  # 当前轮的策略分配
    harness_slots: list[HarnessSlot]              # 所有 slot（跨轮次持久）

    # === 代码生成 ===
    variants: list[DriverVariant]  # 当前轮的变体（每轮被替换）
    all_variants: list[DriverVariant]  # 所有轮次的变体累积（用于报告）

    # === 迭代控制 ===
    round: int                  # 当前轮次（从 0 开始，Analyst 递增）
    max_rounds: int             # 最大轮次数
    max_compile_retries: int    # 每个变体最大编译修复次数
    target_coverage: float      # 目标覆盖率（达到则停止）
    best_coverage: float        # 项目级最佳覆盖率（所有 slot 并集，只升不降）
    best_driver: str            # 历史最佳单 driver 源代码（兼容旧逻辑）
    best_drivers: dict[str, str]  # 每个 slot 的最佳 driver：slot_id → source_code
    coverage_plateau_count: int # 连续无提升轮次数（>=3 停止）
    union_line_covered: int     # 并集覆盖行数
    union_line_total: int       # 总可观测行数
    union_branch_covered: int   # 并集覆盖分支数
    union_branch_total: int     # 总可观测分支数

    # === Analyst 输出（per-slot 隔离） ===
    slot_coverage_analyses: dict[str, dict]  # slot_id → 该 slot 的覆盖率分析
    coverage_analysis: dict     # 兼容字段（deprecated，将被 slot_coverage_analyses 替代）

    # === 运行配置 ===
    fuzz_seconds: int           # 每个变体 fuzzing 时长（秒）
    temperature: float          # LLM 温度参数

    # === 错误处理 ===
    fatal_error: str            # 致命错误（quota exhausted 等），非空时终止 pipeline

    # === Checkpoint ===
    checkpoint_dir: str         # checkpoint 保存目录

    # === 输出 ===
    final_report: dict          # 最终报告数据
    messages: list[str]         # 执行日志（用于 report）
