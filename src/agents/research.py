"""Research Agent: analyzes target library source code to identify fuzzing strategies."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage

from .llm_factory import create_llm
from .state import PipelineState

PROMPT_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.txt").read_text(
    encoding="utf-8"
)


def _read_source_files(target_config: dict) -> str:
    """Read and concatenate target source files for analysis."""
    from target_config import ROOT, resolve_project_path

    source_files = target_config.get("source_files", [])
    header = target_config.get("header", "")
    chunks: list[str] = []

    if header:
        header_path = resolve_project_path(Path(header))
        if header_path.exists():
            chunks.append(f"// === {header} ===\n{header_path.read_text(encoding='utf-8')}")

    for sf in source_files:
        sf_path = resolve_project_path(Path(sf))
        if sf_path.exists():
            content = sf_path.read_text(encoding="utf-8")
            if len(content) > 15000:
                content = content[:15000] + "\n// ... (truncated)"
            chunks.append(f"// === {sf} ===\n{content}")

    return "\n\n".join(chunks) if chunks else "(source files not available)"


def _render_research_prompt(target_config: dict, source_code: str) -> str:
    """Render the research prompt template with target information."""
    return (
        PROMPT_TEMPLATE.replace("{{LIBRARY_NAME}}", target_config.get("target_name", "unknown"))
        .replace("{{FUNCTION_NAME}}", target_config.get("function_name", "unknown"))
        .replace("{{FUNCTION_SIGNATURE}}", target_config.get("signature", "unknown"))
        .replace("{{TARGET_DESCRIPTION}}", target_config.get("description", ""))
        .replace("{{SOURCE_CODE}}", source_code)
    )


def research_node(state: PipelineState) -> dict:
    """LangGraph node: analyze target library and produce research summary."""
    target_config = state["target_config"]

    source_code = _read_source_files(target_config)
    prompt = _render_research_prompt(target_config, source_code)

    llm = create_llm(temperature=0.3)
    response = llm.invoke([HumanMessage(content=prompt)])

    research_summary = response.content if isinstance(response.content, str) else str(response.content)

    return {
        "research_summary": research_summary,
        "source_code_context": source_code[:5000],
        "messages": state.get("messages", []) + [f"[Research] Analyzed {target_config.get('target_name', 'target')}"],
    }
