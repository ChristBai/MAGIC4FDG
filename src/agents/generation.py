"""Generation Agent：基于策略库生成 fuzz driver 变体。

两种模式：
Round 1（from-scratch）：
  为每个 harness slot 从零生成一个 fuzz driver，使用 Planner 选择的策略。
  策略 prompt 从 strategies/*.md 文件加载，拼接到生成模板中。

Round 2+（incremental）：
  基于每个 active slot 的历史最佳 driver 进行增量改进，
  结合 Analyst 输出的约束和未覆盖簇信息，添加新的代码路径。
  不从零生成，而是修改现有代码以保留已有覆盖率。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.knowledge.context_builder import build_generator_context
from src.pipeline.state import DriverVariant, PipelineState, VariantConfig
from src.utils import strip_code_fences

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "strategies"
GENERATION_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "generation_prompt.txt").read_text(
    encoding="utf-8"
)


def _load_strategy_suffix(strategy_id: str) -> str:
    """根据策略 ID 加载策略文件正文（frontmatter 之后的部分），作为 prompt 后缀。"""
    for f in STRATEGIES_DIR.glob("*.md"):
        if f.name == "README.md":
            continue
        content = f.read_text(encoding="utf-8")
        # Check if this file matches the strategy_id
        if f"id: {strategy_id}" in content[:500]:
            # Return body after frontmatter
            end = content.find("---", 3)
            if end > 0:
                return content[end + 3:].strip()
    return ""


def generation_node(state: PipelineState) -> dict:
    """LangGraph 节点：为每个 strategy selection 生成一个 fuzz driver 变体。"""
    target_config = state["target_config"]
    knowledge = state.get("knowledge", {})
    selections = state.get("strategy_selections", [])
    slots = state.get("harness_slots", [])
    round_num = state.get("round", 0)
    temperature = state.get("temperature", 0.4)
    coverage_analysis = state.get("coverage_analysis", {})

    is_incremental = round_num > 0
    mode = "incremental" if is_incremental else "from-scratch"
    print(f"[Generation] Round {round_num + 1}, mode={mode}, {len(selections)} variants", flush=True)

    variants: list[DriverVariant] = []
    messages = list(state.get("messages", []))
    messages.append(f"[Generation] Starting round {round_num + 1} ({mode})")

    max_workers = int(os.environ.get("MAGIC4FDG_LLM_PARALLEL", os.environ.get("MAGIC4FDG_PARALLEL", "10")))

    if not selections:
        return {"variants": [], "messages": messages}

    def _generate_one(i: int, selection):
        slot_id = selection["slot_id"]
        strategy_id = selection["strategy_id"]
        variant_id = f"{slot_id}_{strategy_id}_r{round_num}"

        slot = next((s for s in slots if s["slot_id"] == slot_id), None)
        slot_knowledge = knowledge.get("slot_knowledge", {}).get(slot_id, {})
        knowledge_context = build_generator_context(knowledge, selection, slot, slot_knowledge)
        strategy_suffix = _load_strategy_suffix(strategy_id)
        slot_analysis = state.get("slot_coverage_analyses", {}).get(slot_id, coverage_analysis)

        if is_incremental and slot and slot.get("best_source"):
            prompt = _build_incremental_prompt(
                target_config, knowledge_context, strategy_suffix,
                slot["best_source"], slot_analysis,
            )
        else:
            prompt = _build_fresh_prompt(target_config, knowledge_context, strategy_suffix)

        source_code = ""
        error_msg = ""
        try:
            llm = create_llm(temperature=temperature)
            response = llm.invoke([
                SystemMessage(content="You are an expert C/C++ security engineer. Output only valid C++ source code."),
                HumanMessage(content=prompt),
            ])
            prompt_tok, completion_tok = extract_token_usage(response)
            get_tracker().record("generation", "default", prompt_tok, completion_tok)
            raw_code = response.content if isinstance(response.content, str) else str(response.content)
            source_code = strip_code_fences(raw_code)
        except Exception as e:
            error_msg = str(e)

        return i, variant_id, slot_id, strategy_id, source_code, error_msg

    with ThreadPoolExecutor(max_workers=min(len(selections), max_workers)) as pool:
        futures = [
            pool.submit(_generate_one, i, sel)
            for i, sel in enumerate(selections, 1)
        ]
        results = []
        for future in as_completed(futures):
            results.append(future.result())

    for i, variant_id, slot_id, strategy_id, source_code, error_msg in sorted(results):
        if error_msg:
            print(f"[Generation]   FAILED {variant_id}: {error_msg}", flush=True)
            messages.append(f"[Generation] Failed {variant_id}: {error_msg}")
        else:
            print(f"[Generation]   ({i}/{len(selections)}) {variant_id} OK", flush=True)

        variant: DriverVariant = {
            "id": variant_id,
            "slot_id": slot_id,
            "config": VariantConfig(
                model="default",
                prompt_strategy=strategy_id,
                temperature=temperature,
            ),
            "source_code": source_code,
            "compile_status": "pending" if source_code else "failed",
            "compile_errors": "" if source_code else f"Generation failed: {error_msg}",
            "patch_attempts": 0,
            "coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "uncovered_lines": [],
            "covered_lines": [],
            "function_coverage": [],
            "is_incremental": is_incremental,
        }
        variants.append(variant)
        messages.append(f"[Generation] Generated {variant_id} ({len(source_code)} chars)")

    # Detect fatal API errors (quota exhausted, auth failure) — abort pipeline
    _FATAL_PATTERNS = ("insufficient_user_quota", "insufficient_quota", "403", "401", "authentication")
    error_msgs = [em for _, _, _, _, _, em in results if em]
    fatal_errors = [em for em in error_msgs if any(p in em.lower() for p in _FATAL_PATTERNS)]
    fatal_error = ""
    if fatal_errors and len(fatal_errors) == len(error_msgs) and len(error_msgs) == len(results):
        fatal_error = f"All LLM calls failed with fatal error: {fatal_errors[0][:200]}"
        print(f"[Generation] FATAL: {fatal_error}", flush=True)

    return {
        "variants": variants,
        "messages": messages,
        "fatal_error": fatal_error,
    }


def _build_fresh_prompt(target_config: dict, knowledge_context: str, strategy_suffix: str) -> str:
    """构建 Round 1 从零生成的 prompt（模板 + 知识上下文 + 策略后缀）。"""
    include_dirs = target_config.get("include_dirs", [])
    prompt = (
        GENERATION_TEMPLATE
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{INCLUDE_DIRS}}", ", ".join(include_dirs))
        .replace("{{RESEARCH_SUMMARY}}", knowledge_context)
        .replace("{{COVERAGE_FEEDBACK}}", "(first round — no prior coverage data)")
    )
    if strategy_suffix:
        prompt += "\n\n" + strategy_suffix
    return prompt


def _build_incremental_prompt(
    target_config: dict,
    knowledge_context: str,
    strategy_suffix: str,
    base_source: str,
    coverage_analysis: dict,
) -> str:
    """构建 Round 2+ 增量改进的 prompt（现有代码 + Analyst 约束 + 知识上下文）。"""
    constraints_text = ""
    if coverage_analysis:
        constraints = coverage_analysis.get("constraints", [])
        if constraints:
            constraints_text = "\n".join(
                f"- {c.get('target', '?')}: {c.get('precondition', '')}"
                for c in constraints[:10]
            )

    clusters_text = ""
    if coverage_analysis:
        clusters = coverage_analysis.get("uncovered_clusters", [])
        if clusters:
            clusters_text = "\n".join(
                f"- {', '.join(c.get('functions', []))}: {c.get('root_cause', '')}"
                for c in clusters[:5]
            )

    prompt = f"""## Task: Improve Fuzz Driver for {target_config.get("library_name", "")}

You have an existing fuzz driver that achieves partial coverage. Your task is to
MODIFY it to cover additional code paths identified by the analyst.

## Current Driver (to be improved)

```cpp
{base_source}
```

## Analyst Constraints (must address these)

{constraints_text or "(no specific constraints)"}

## Uncovered Code Clusters

{clusters_text or "(no cluster data)"}

## API Knowledge

{knowledge_context}

## Strategy Guidance

{strategy_suffix}

## Instructions

1. Keep the parts of the current driver that work well
2. ADD new code paths that address the analyst constraints above
3. Do NOT remove existing coverage — only add new coverage
4. Ensure proper memory management (free all allocated resources)
5. Output a complete, compilable fuzz driver

Output ONLY the complete C/C++ source code for the improved driver.
"""
    return prompt
