"""LangGraph state graph definition for the multi-agent fuzz driver pipeline."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .research import research_node
from .state import PipelineState


def build_graph() -> StateGraph:
    """Build the multi-agent pipeline graph.

    Current implementation: Research only (will be extended in subsequent iterations).
    """
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)

    graph.set_entry_point("research")
    graph.add_edge("research", END)

    return graph


def compile_graph():
    """Compile the graph into a runnable."""
    return build_graph().compile()
