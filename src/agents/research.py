"""Research Agent: analyzes target library source code to identify fuzzing strategies."""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.pipeline.state import PipelineState
from src.config import resolve_project_path

PROMPT_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "research_prompt.txt").read_text(
    encoding="utf-8"
)

_FUNC_DECL_RE = re.compile(
    r"^\s*(?:CJSON_PUBLIC\(([^)]+)\)|extern\s+)?\s*"
    r"([\w*\s]+?)\s+(\w+)\s*\([^)]*\)\s*;",
    re.MULTILINE,
)


def _extract_signatures(header_text: str) -> str:
    """Extract function declarations from a C/C++ header file."""
    lines = []
    for line in header_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            continue
        if "(" in stripped and stripped.endswith(";") and not stripped.startswith("#"):
            lines.append(stripped)
    result = "\n".join(lines)
    if len(result) > 2000:
        result = result[:2000] + "\n// ... (truncated)"
    return result if result else header_text[:1500]


def _read_source_files(target_config: dict) -> str:
    """Extract API signatures from header for LLM analysis."""
    header = target_config.get("header", "")
    if not header:
        return "(no header specified)"

    header_path = resolve_project_path(Path(header))
    if not header_path.exists():
        return "(header file not found)"

    h_content = header_path.read_text(encoding="utf-8")
    return _extract_signatures(h_content)


def _render_research_prompt(target_config: dict, source_code: str) -> str:
    """Render the research prompt template with target information."""
    return (
        PROMPT_TEMPLATE
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", "unknown"))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{DESCRIPTION}}", target_config.get("description", ""))
        .replace("{{SOURCE_CODE}}", source_code)
    )


def research_node(state: PipelineState) -> dict:
    """LangGraph node: analyze target library and produce research summary."""
    target_config = state["target_config"]

    source_code = _read_source_files(target_config)
    prompt = _render_research_prompt(target_config, source_code)

    llm = create_llm(temperature=0.3)
    response = llm.invoke([HumanMessage(content=prompt)])

    prompt_tok, completion_tok = extract_token_usage(response)
    get_tracker().record("research", "default", prompt_tok, completion_tok)

    research_summary = response.content if isinstance(response.content, str) else str(response.content)

    return {
        "research_summary": research_summary,
        "source_code_context": source_code[:5000],
        "messages": state.get("messages", []) + [
            f"[Research] Analyzed library: {target_config.get('library_name', 'unknown')}"
        ],
    }
