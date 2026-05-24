"""LangGraph 状态图定义：MAGIC4FDG v2 多 agent 流水线。

本文件定义 pipeline 的执行拓扑（DAG），是整个系统的"骨架"。
每个节点是一个 agent 函数，接收 PipelineState、返回部分更新。
LangGraph 负责按拓扑顺序调度节点、合并状态、处理条件分支。

DAG 拓扑：
  Knowledge → Planner → Generation → Patching ─┬─→ Coverage → Checkpoint → Analyst
                                                │                              ↓
                                                │                    Supervisor Decision
                                                │                   /        |         \
                                                └→ Checkpoint(跳过coverage) END  Planner(R2+)  END
                                                        ↓                          ↓
                                                     Analyst                  Generation → ...

两个条件分支点：
1. Patching 之后：有变体编译成功 → Coverage；全部失败 → 跳过 Coverage 直接到 Checkpoint
2. Analyst 之后（Supervisor 决策）：
   - 达到目标覆盖率 → END
   - 超过最大轮次 → END
   - 覆盖率连续 3 轮无提升（plateau） → END
   - 所有 slot 已 converged → END
   - 否则 → 回到 Planner 开始下一轮（exploit 模式）
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.analyst import analyst_node
from src.agents.coverage import coverage_node
from src.agents.generation import generation_node
from src.agents.knowledge import knowledge_node
from src.agents.patching import patching_node
from src.agents.planner import planner_node
from src.pipeline.checkpoint import checkpoint_node
from src.pipeline.state import PipelineState


def _route_after_patching(state: PipelineState) -> str:
    """Patching 后的路由。

    有变体编译成功 → 进入 coverage 正常流程；
    全部失败 → 跳过 coverage 直接进入 checkpoint → analyst，
    让 analyst 记录失败原因，supervisor 决定是否换策略重试。
    """
    variants = state.get("variants", [])
    if any(v["compile_status"] == "ok" for v in variants):
        return "coverage"
    return "checkpoint"


def _route_after_analyst(state: PipelineState) -> str:
    """Supervisor 决策：判断是否继续迭代。

    五个停止条件（任一满足即终止）：
    1. Fatal error（API quota exhausted, auth failure）
    2. 已达到目标覆盖率（项目级并集）
    3. 已达到最大轮次
    4. 全部 harness slot 已 converged（各自 plateau 后停止）
    5. Pipeline 级 plateau >= 3（项目级覆盖率连续无提升）
    """
    if state.get("fatal_error"):
        print(f"[Supervisor] Aborting: {state['fatal_error']}", flush=True)
        return END

    best_coverage = state.get("best_coverage", 0.0)
    target_coverage = state.get("target_coverage", 100.0)

    if best_coverage >= target_coverage:
        return END

    round_num = state.get("round", 0)
    max_rounds = state.get("max_rounds", 10)
    if round_num >= max_rounds:
        return END

    slots = state.get("harness_slots", [])
    active = [s for s in slots if s.get("status") == "active"]
    if not active:
        return END

    plateau = state.get("coverage_plateau_count", 0)
    if plateau >= 3:
        return END

    return "planner"


def build_graph() -> StateGraph:
    """构建 v2 多 agent 流水线图。"""
    graph = StateGraph(PipelineState)

    graph.add_node("knowledge", knowledge_node)
    graph.add_node("planner", planner_node)
    graph.add_node("generation", generation_node)
    graph.add_node("patching", patching_node)
    graph.add_node("coverage", coverage_node)
    graph.add_node("checkpoint", checkpoint_node)
    graph.add_node("analyst", analyst_node)

    graph.set_entry_point("knowledge")
    graph.add_edge("knowledge", "planner")
    graph.add_edge("planner", "generation")
    graph.add_edge("generation", "patching")
    graph.add_conditional_edges("patching", _route_after_patching)
    graph.add_edge("coverage", "checkpoint")
    graph.add_edge("checkpoint", "analyst")
    graph.add_conditional_edges("analyst", _route_after_analyst)

    return graph


def compile_graph():
    """编译图为可执行对象。"""
    return build_graph().compile()
