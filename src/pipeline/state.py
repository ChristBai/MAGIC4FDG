"""Pipeline state definitions for the multi-agent fuzz driver generation system.

Defines the TypedDicts that flow through the LangGraph StateGraph:
- VariantConfig: model/strategy/temperature for a single generation call
- DriverVariant: a fuzz driver with its compilation and coverage status
- PipelineState: the full state shared across all agent nodes
"""

from __future__ import annotations

from typing import Literal, TypedDict


class VariantConfig(TypedDict):
    """Configuration for a single driver variant generation."""

    model: str
    prompt_strategy: str
    temperature: float


class DriverVariant(TypedDict):
    """A single fuzz driver variant with its compilation and coverage status."""

    id: str
    config: VariantConfig
    source_code: str
    compile_status: Literal["pending", "ok", "failed"]
    compile_errors: str
    patch_attempts: int
    coverage_pct: float
    branch_coverage_pct: float
    uncovered_lines: list[dict]
    covered_lines: list[dict]
    function_coverage: list[dict]
    unique_coverage: list[int]


class PipelineState(TypedDict):
    """Full state flowing through the LangGraph pipeline."""

    # Input
    target_config: dict
    target_config_path: str
    # Research output
    research_summary: str
    source_code_context: str
    reachable_branches: list[dict]
    # Generation / Patching
    variant_matrix: list[VariantConfig]
    variants: list[DriverVariant]
    # Iteration control
    iteration: int
    max_iterations: int
    max_compile_retries: int
    target_coverage: float
    best_coverage: float
    best_driver: str
    # Coverage feedback for next generation round
    coverage_feedback: str
    # Temperature schedule
    temperature_schedule: list[float]
    current_temp_idx: int
    # Configuration
    fuzz_seconds: int
    # Output
    final_report: dict
    messages: list[str]
