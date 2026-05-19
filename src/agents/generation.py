"""Generation Agent: produces multiple fuzz driver variants using different strategies.

Generates N variants = models × strategies at a fixed temperature per round.
Three literature-backed strategies produce structurally different drivers:
- parse: Feed raw fuzz bytes to parsing APIs (PromptFuzz/FUDGE style)
- api-chain: Multi-API call sequences with state transitions (CKGFuzzer style)
- roundtrip: Parse → modify → serialize → re-parse (MUTATO style)

In the temperature-escalation iteration model, each round uses a different
temperature from the schedule (0.4 → 0.7 → 0.9). The strategy suffix in the
prompt has the most significant impact on coverage outcomes.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm_factory import create_variant_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.pipeline.state import DriverVariant, PipelineState, VariantConfig
from src.utils import strip_code_fences

GENERATION_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "generation_prompt.txt").read_text(
    encoding="utf-8"
)

STRATEGY_SUFFIXES = {
    "parse": """
## Strategy: Parse-Centric (PromptFuzz-style)

Feed raw fuzz bytes directly to ALL parsing API variants. Maximize parser path coverage:
- Call every parse function variant (with/without length, with/without opts)
- Use different option combinations (require_null_terminated = true/false)
- Exercise error paths by feeding truncated/malformed data
- After successful parse, do minimal downstream ops (Print, GetArraySize) to trigger post-parse paths
- Do NOT focus on construction APIs — prioritize parser internals
""",
    "api-chain": """
## Strategy: API-Chain (CKGFuzzer-style)

Use fuzz bytes to drive multi-API call sequences covering state transitions:
- Use first bytes as path selectors (switch on data[0] % N)
- Each path exercises a different API combination chain (create→add→query→modify→delete)
- Cover ownership transfer (DetachItem→AddItem to different parent)
- Cover edge cases: empty containers, index out of bounds, NULL keys
- Allocate via Create* APIs, modify via Add*/Replace*/Delete*, query via Get*/Has*, cleanup via Delete
- Do NOT just parse — focus on construction and manipulation APIs
- IMPORTANT: Do NOT call *_InitHooks or similar hook-setting APIs unless you provide valid malloc/free function pointers
""",
    "roundtrip": """
## Strategy: Round-Trip (MUTATO-style)

Parse → Modify → Serialize → Re-parse to cover both directions:
- Parse fuzz input into internal representation
- Apply mutations driven by fuzz bytes (add fields, delete fields, replace values, change types)
- Serialize back to string (both Print and PrintUnformatted)
- Re-parse the serialized output
- Compare or further manipulate the re-parsed result
- This exercises serializer formatting, escaping, buffer management paths that parse-only never reaches
- IMPORTANT: Do NOT call *_InitHooks or similar hook-setting APIs unless you provide valid malloc/free function pointers
""",
    "targeted": """
## Strategy: Targeted Coverage Expansion

Based on the coverage feedback above, specifically target the UNCOVERED functions and branches:
- For each zero-coverage function listed, construct valid arguments and call it
- For each uncovered line shown with source context, reason about what API call sequence reaches it
- Chain API calls that lead to the uncovered code paths
- Use fuzz bytes to vary arguments and trigger different branches within targeted functions
- Prioritize functions with 0% coverage over those with partial coverage
- If no coverage feedback is available yet, use the api-chain approach: multi-API call sequences with state transitions
- IMPORTANT: Do NOT call *_InitHooks or similar hook-setting APIs unless you provide valid malloc/free function pointers
""",
}


def _build_prompt(target_config: dict, strategy: str, research_summary: str, coverage_feedback: str = "") -> str:
    """Build the generation prompt based on strategy."""
    include_dirs = target_config.get("include_dirs", [])
    base_prompt = (
        GENERATION_TEMPLATE
        .replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{LANGUAGE}}", target_config.get("language", "C"))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{INCLUDE_DIRS}}", ", ".join(include_dirs))
        .replace("{{RESEARCH_SUMMARY}}", research_summary or "(not available)")
        .replace("{{COVERAGE_FEEDBACK}}", coverage_feedback or "(first iteration — no prior coverage data)")
    )

    suffix = STRATEGY_SUFFIXES.get(strategy, "")
    return base_prompt + suffix


def _make_variant_id(config: VariantConfig) -> str:
    """Generate a human-readable variant ID from config."""
    model_short = config["model"].replace("-", "").replace(".", "")[:8]
    temp_str = str(config["temperature"]).replace(".", "")
    return f"{model_short}_{config['prompt_strategy']}_t{temp_str}"


def generation_node(state: PipelineState) -> dict:
    """LangGraph node: generate multiple fuzz driver variants.

    Uses the current temperature from the temperature schedule.
    Each iteration generates models × strategies variants at a single temperature.
    """
    target_config = state["target_config"]
    research_summary = state.get("research_summary", "")
    coverage_feedback = state.get("coverage_feedback", "")

    temp_schedule = state.get("temperature_schedule", [0.7])
    temp_idx = state.get("current_temp_idx", 0)
    current_temp = temp_schedule[min(temp_idx, len(temp_schedule) - 1)]

    variant_configs = _build_variant_configs(current_temp)
    print(f"[Generation] Round {temp_idx + 1}, temp={current_temp}, {len(variant_configs)} variants", flush=True)

    variants: list[DriverVariant] = list(state.get("variants", []))
    messages = list(state.get("messages", []))
    messages.append(f"[Generation] Starting round {temp_idx + 1} with temperature={current_temp}")

    for i, config in enumerate(variant_configs, 1):
        variant_id = _make_variant_id(config)
        print(f"[Generation]   ({i}/{len(variant_configs)}) {variant_id}...", flush=True)
        prompt = _build_prompt(target_config, config["prompt_strategy"], research_summary, coverage_feedback)

        try:
            llm = create_variant_llm(config["model"], config["temperature"])
            for _attempt in range(3):
                try:
                    response = llm.invoke([
                        SystemMessage(content="You are an expert C/C++ security engineer. Output only valid C++ source code."),
                        HumanMessage(content=prompt),
                    ])
                    break
                except Exception as retry_err:
                    if _attempt < 2:
                        import time
                        print(f"[Generation]   retry {_attempt+1}/3 for {variant_id}...", flush=True)
                        time.sleep(5 * (_attempt + 1))
                    else:
                        raise retry_err
            prompt_tok, completion_tok = extract_token_usage(response)
            get_tracker().record("generation", config["model"], prompt_tok, completion_tok)
            raw_code = response.content if isinstance(response.content, str) else str(response.content)
            source_code = strip_code_fences(raw_code)
        except Exception as e:
            source_code = ""
            print(f"[Generation]   FAILED {variant_id}: {e}", flush=True)
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
            "covered_lines": [],
            "function_coverage": [],
            "unique_coverage": [],
            "iteration": temp_idx,
        }
        variants.append(variant)
        messages.append(f"[Generation] Generated {variant_id} ({len(source_code)} chars)")

    return {
        "variants": variants,
        "messages": messages,
    }


def _build_variant_configs(temperature: float) -> list[VariantConfig]:
    """Build variant configs for a single temperature (models × strategies)."""
    from src.infra.llm_factory import _load_config

    config = _load_config()
    models_cfg = config.get("models", [])
    matrix_cfg = config.get("variant_matrix", {})

    strategies = matrix_cfg.get("strategies", ["parse", "api-chain", "roundtrip"])

    if not models_cfg:
        import os
        model = os.environ.get("LLM_MODEL", "gpt-4o")
        models_cfg = [{"name": model}]

    matrix: list[VariantConfig] = []
    for model_info in models_cfg:
        for strategy in strategies:
            matrix.append({
                "model": model_info["name"],
                "prompt_strategy": strategy,
                "temperature": temperature,
            })
    return matrix


def _default_variant_matrix() -> list[VariantConfig]:
    """Build variant matrix for the first iteration (uses first temperature).

    Kept for backward compatibility with supervisor initial state.
    """
    from src.infra.llm_factory import _load_config

    config = _load_config()
    matrix_cfg = config.get("variant_matrix", {})
    temperatures = matrix_cfg.get("temperatures", [0.7])
    return _build_variant_configs(temperatures[0])


def get_temperature_schedule() -> list[float]:
    """Get the temperature schedule from config."""
    from src.infra.llm_factory import _load_config

    config = _load_config()
    matrix_cfg = config.get("variant_matrix", {})
    return matrix_cfg.get("temperatures", [0.7])
