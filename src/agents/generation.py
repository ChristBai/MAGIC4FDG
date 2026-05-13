"""Generation Agent: produces multiple fuzz driver variants using different strategies."""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_factory import create_variant_llm
from .state import DriverVariant, PipelineState, VariantConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_driver import render_prompt, strip_code_fences
from target_config import ROOT

EXAMPLE_PROMPT_SUFFIX = """

Here is an example of a well-written fuzz driver for a similar function:

```cpp
#include <cstdint>
#include <cstddef>
#include <cstring>
#include "target.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;
    // Create null-terminated copy for string APIs
    char *buf = new char[size + 1];
    memcpy(buf, data, size);
    buf[size] = '\\0';
    // Call target with fuzz data
    auto *result = target_parse(buf);
    if (result) target_free(result);
    delete[] buf;
    return 0;
}
```

Generate a driver following this pattern but adapted to the specific target function.
"""


def _build_prompt(target_config: dict, strategy: str, research_summary: str) -> str:
    """Build the generation prompt based on strategy."""
    base_prompt = render_prompt(target_config)

    if strategy == "basic":
        return base_prompt

    if strategy == "research":
        return (
            base_prompt
            + "\n\n## Additional Context from Code Analysis\n\n"
            + research_summary
            + "\n\nUse the above analysis to generate a driver that exercises diverse code paths."
        )

    if strategy == "example":
        return base_prompt + EXAMPLE_PROMPT_SUFFIX

    return base_prompt


def _make_variant_id(config: VariantConfig) -> str:
    """Generate a human-readable variant ID from config."""
    model_short = config["model"].replace("-", "").replace(".", "")[:8]
    temp_str = str(config["temperature"]).replace(".", "")
    return f"{model_short}_{config['prompt_strategy']}_t{temp_str}"


def generation_node(state: PipelineState) -> dict:
    """LangGraph node: generate multiple fuzz driver variants."""
    target_config = state["target_config"]
    research_summary = state.get("research_summary", "")
    variant_matrix = state.get("variant_matrix", [])

    if not variant_matrix:
        variant_matrix = _default_variant_matrix()

    variants: list[DriverVariant] = []
    messages = list(state.get("messages", []))

    for config in variant_matrix:
        variant_id = _make_variant_id(config)
        prompt = _build_prompt(target_config, config["prompt_strategy"], research_summary)

        try:
            llm = create_variant_llm(config["model"], config["temperature"])
            response = llm.invoke([
                SystemMessage(content="You are an expert C/C++ security engineer. Output only valid C++ source code."),
                HumanMessage(content=prompt),
            ])
            raw_code = response.content if isinstance(response.content, str) else str(response.content)
            source_code = strip_code_fences(raw_code)
        except Exception as e:
            source_code = ""
            messages.append(f"[Generation] Failed {variant_id}: {e}")

        variant: DriverVariant = {
            "id": variant_id,
            "config": config,
            "source_code": source_code,
            "compile_status": "pending" if source_code else "failed",
            "compile_errors": "" if source_code else "Generation failed",
            "patch_attempts": 0,
            "coverage_pct": 0.0,
            "branch_coverage_pct": 0.0,
            "uncovered_lines": [],
            "unique_coverage": [],
        }
        variants.append(variant)
        messages.append(f"[Generation] Generated {variant_id} ({len(source_code)} chars)")

    return {
        "variants": variants,
        "messages": messages,
    }


def _default_variant_matrix() -> list[VariantConfig]:
    """Build variant matrix from llm_config.json.

    Generates: len(models) × len(strategies) × len(temperatures) variants.
    Falls back to environment variables if config file is missing.
    """
    from .llm_factory import _load_config

    config = _load_config()
    models_cfg = config.get("models", [])
    matrix_cfg = config.get("variant_matrix", {})

    strategies = matrix_cfg.get("strategies", ["basic", "research", "example"])
    temperatures = matrix_cfg.get("temperatures", [0.7])

    if not models_cfg:
        import os
        model = os.environ.get("LLM_MODEL", "gpt-4o")
        models_cfg = [{"name": model}]

    matrix: list[VariantConfig] = []
    for model_info in models_cfg:
        for strategy in strategies:
            for temp in temperatures:
                matrix.append({
                    "model": model_info["name"],
                    "prompt_strategy": strategy,
                    "temperature": temp,
                })
    return matrix
