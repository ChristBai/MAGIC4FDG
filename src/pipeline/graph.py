"""LangGraph state graph definition for the multi-agent fuzz driver pipeline.

Defines the DAG of agent nodes and routing logic:
  Research → Generation → Patching → Coverage
                                        ↓
                              target met or temps exhausted → END
                                        ↓
                              Refinement → Generation → Patching → Coverage → ...

Routing after coverage checks: target coverage reached, temperature schedule
exhausted, or max_iterations exceeded. If none, routes to refinement which
advances the temperature index and triggers a new generation round.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.coverage import coverage_node
from src.agents.generation import generation_node
from src.agents.patching import patching_node
from src.agents.refinement import refinement_node
from src.agents.research import research_node
from src.pipeline.state import PipelineState


def _route_after_patching(state: PipelineState) -> str:
    """Route after patching: proceed to coverage if any variant compiled."""
    variants = state.get("variants", [])
    if any(v["compile_status"] == "ok" for v in variants):
        return "coverage"
    return END


def _route_after_coverage(state: PipelineState) -> str:
    """Route after coverage: end if target met or all temperatures exhausted."""
    best_coverage = state.get("best_coverage", 0.0)
    target_coverage = state.get("target_coverage", 70.0)

    if best_coverage >= target_coverage:
        return END

    temp_schedule = state.get("temperature_schedule", [0.7])
    current_idx = state.get("current_temp_idx", 0)
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 10)

    if current_idx >= len(temp_schedule) - 1:
        return END
    if iteration >= max_iterations:
        return END

    return "refinement"


def build_graph() -> StateGraph:
    """Build the multi-agent pipeline graph.

    Flow: Research → Generation → Patching → Coverage
                                                ↓
                                      target met or temps exhausted → END
                                                ↓
                                      Refinement → Generation → Patching → Coverage → ...
    """
    graph = StateGraph(PipelineState)

    graph.add_node("research", research_node)
    graph.add_node("generation", generation_node)
    graph.add_node("patching", patching_node)
    graph.add_node("coverage", coverage_node)
    graph.add_node("refinement", refinement_node)

    graph.set_entry_point("research")
    graph.add_edge("research", "generation")
    graph.add_edge("generation", "patching")
    graph.add_conditional_edges("patching", _route_after_patching)
    graph.add_conditional_edges("coverage", _route_after_coverage)
    graph.add_edge("refinement", "generation")

    return graph


def compile_graph():
    """Compile the graph into a runnable."""
    return build_graph().compile()
