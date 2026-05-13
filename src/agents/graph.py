"""LangGraph state graph definition for the multi-agent fuzz driver pipeline."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .generation import generation_node
from .research import research_node
from .state import PipelineState


def build_graph() -> StateGraph:
    """Build the multi-agent pipeline graph.

    Current: Research → Generation → END
    """
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)
    graph.add_node("generation", generation_node)

    graph.set_entry_point("research")
    graph.add_edge("research", "generation")
    graph.add_edge("generation", END)

    return graph


def compile_graph():
    """Compile the graph into a runnable."""
    return build_graph().compile()
