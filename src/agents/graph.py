"""LangGraph state graph definition for the multi-agent fuzz driver pipeline."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .generation import generation_node
from .patching import patching_node
from .research import research_node
from .state import PipelineState


def _route_after_patching(state: PipelineState) -> str:
    """Route after patching: proceed if any variant compiled, else end."""
    variants = state.get("variants", [])
    if any(v["compile_status"] == "ok" for v in variants):
        return END  # will route to "coverage" once implemented
    return END


def build_graph() -> StateGraph:
    """Build the multi-agent pipeline graph.

    Current: Research → Generation → Patching → END
    """
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)
    graph.add_node("generation", generation_node)
    graph.add_node("patching", patching_node)

    graph.set_entry_point("research")
    graph.add_edge("research", "generation")
    graph.add_edge("generation", "patching")
    graph.add_conditional_edges("patching", _route_after_patching)

    return graph


def compile_graph():
    """Compile the graph into a runnable."""
    return build_graph().compile()
