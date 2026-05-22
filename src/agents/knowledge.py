"""Knowledge Agent：通过 clang AST 提取目标库的结构化 API 知识。

Pipeline 启动时执行一次，不调用 LLM——纯静态分析。
在 Docker 容器内运行 clang 命令，提取函数签名、类型定义、
调用图、宏常量，并推断 API 分类和 ownership 语义。

输出填充 KnowledgeStore，供下游所有 agent 使用。
"""

from __future__ import annotations

from src.knowledge.extractor import extract_knowledge
from src.pipeline.state import PipelineState


def knowledge_node(state: PipelineState) -> dict:
    """LangGraph 节点：从目标库头文件和源文件中提取结构化知识。"""
    target_config = state["target_config"]
    lib_name = target_config.get("library_name", "?")
    print(f"[Knowledge] Extracting API knowledge for {lib_name}...", flush=True)

    knowledge = extract_knowledge(target_config)

    n_apis = len(knowledge["api_entries"])
    n_types = len(knowledge["type_definitions"])
    n_edges = sum(len(v) for v in knowledge["call_graph"].values())
    print(
        f"[Knowledge] Done: {n_apis} APIs, {n_types} types, {n_edges} call edges",
        flush=True,
    )

    return {
        "knowledge": knowledge,
        "messages": state.get("messages", []) + [
            f"[Knowledge] Extracted {n_apis} APIs, {n_types} types for {lib_name}"
        ],
    }
