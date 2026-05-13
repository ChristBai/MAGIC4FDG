"""LangGraph state graph definition for the multi-agent fuzz driver pipeline."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .coverage import coverage_node
from .generation import generation_node
from .patching import patching_node
from .research import research_node
from .state import PipelineState


def _route_after_patching(state: PipelineState) -> str:
    """Route after patching: proceed to coverage if any variant compiled."""
    variants = state.get("variants", [])
    if any(v["compile_status"] == "ok" for v in variants):
        return "coverage"
    return END


def _route_after_coverage(state: PipelineState) -> str:
    """Route after coverage: end if target met or max iterations reached."""
    best_coverage = state.get("best_coverage", 0.0)
    target_coverage = state.get("target_coverage", 70.0)
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)

    if best_coverage >= target_coverage:
        return END
    if iteration >= max_iterations:
        return END
    return END  # will route to "refinement" once implemented


def build_graph() -> StateGraph:
    """Build the multi-agent pipeline graph.

    Research → Generation → Patching → Coverage → END
    """
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)
    graph.add_node("generation", generation_node)
    graph.add_node("patching", patching_node)
    graph.add_node("coverage", coverage_node)

    graph.set_entry_point("research")
    graph.add_edge("research", "generation")
    graph.add_edge("generation", "patching")
    graph.add_conditional_edges("patching", _route_after_patching)
    graph.add_conditional_edges("coverage", _route_after_coverage)

    return graph


def compile_graph():
    """Compile the graph into a runnable."""
    return build_graph().compile()
