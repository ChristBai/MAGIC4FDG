"""Patching Agent: fixes compilation errors in generated fuzz drivers.

For each variant with compile_status="pending", attempts Docker compilation.
If compilation fails, sends the source code + error messages to an LLM for
repair, retrying up to max_compile_retries times. Successfully compiled
variants get compile_status="ok" and proceed to coverage measurement.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.docker_runner import compile_driver
from src.infra.llm_factory import create_llm
from src.infra.token_tracker import extract_token_usage, get_tracker
from src.pipeline.state import DriverVariant, PipelineState
from src.utils import strip_code_fences

PATCH_TEMPLATE = (Path(__file__).resolve().parents[2] / "prompts" / "patching_prompt.txt").read_text(
    encoding="utf-8"
)


def _render_patch_prompt(target_config: dict, driver_code: str, compile_errors: str) -> str:
    """Render the patching prompt with error context."""
    include_dirs = target_config.get("include_dirs", [])
    return (
        PATCH_TEMPLATE.replace("{{LIBRARY_NAME}}", target_config.get("library_name", ""))
        .replace("{{HEADER}}", target_config.get("header", ""))
        .replace("{{INCLUDE_DIRS}}", ", ".join(include_dirs))
        .replace("{{DRIVER_CODE}}", driver_code)
        .replace("{{COMPILE_ERRORS}}", compile_errors)
        # Legacy placeholders - keep for backward compat with prompt file
        .replace("{{FUNCTION_NAME}}", target_config.get("library_name", ""))
        .replace("{{FUNCTION_SIGNATURE}}", "")
    )


def _try_compile(variant: DriverVariant, target_config: dict) -> tuple[bool, str]:
    """Attempt to compile a variant. Returns (success, errors)."""
    driver_filename = f"patch_{variant['id']}.cpp"
    return compile_driver(variant["source_code"], target_config, driver_filename)


def _patch_single_variant(
    variant: DriverVariant,
    target_config: dict,
    max_retries: int,
    messages: list[str],
) -> DriverVariant:
    """Attempt to compile and patch a single variant."""
    if not variant["source_code"]:
        variant["compile_status"] = "failed"
        variant["compile_errors"] = "Empty source code"
        return variant

    success, errors = _try_compile(variant, target_config)

    if success:
        variant["compile_status"] = "ok"
        variant["compile_errors"] = ""
        messages.append(f"[Patching] {variant['id']} compiled successfully on first try")
        return variant

    llm = create_llm(temperature=0.2)

    for attempt in range(max_retries):
        variant["patch_attempts"] = attempt + 1
        variant["compile_errors"] = errors

        prompt = _render_patch_prompt(target_config, variant["source_code"], errors)
        response = llm.invoke([
            SystemMessage(content="You are a C/C++ compilation error fixer. Output only valid C++ code."),
            HumanMessage(content=prompt),
        ])

        prompt_tok, completion_tok = extract_token_usage(response)
        get_tracker().record("patching", variant["config"]["model"], prompt_tok, completion_tok)

        raw = response.content if isinstance(response.content, str) else str(response.content)
        variant["source_code"] = strip_code_fences(raw)

        success, errors = _try_compile(variant, target_config)

        if success:
            variant["compile_status"] = "ok"
            variant["compile_errors"] = ""
            messages.append(
                f"[Patching] {variant['id']} fixed after {attempt + 1} attempt(s)"
            )
            return variant

        messages.append(
            f"[Patching] {variant['id']} attempt {attempt + 1}/{max_retries} failed"
        )

    variant["compile_status"] = "failed"
    variant["compile_errors"] = errors
    messages.append(f"[Patching] {variant['id']} failed after {max_retries} attempts")
    return variant


def patching_node(state: PipelineState) -> dict:
    """LangGraph node: attempt to compile each variant, fix errors if needed."""
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    max_retries = state.get("max_compile_retries", 3)
    messages = list(state.get("messages", []))

    pending = [v for v in variants if v["compile_status"] == "pending"]
    print(f"[Patching] {len(pending)} pending variants to compile", flush=True)

    patched_variants: list[DriverVariant] = []

    for variant in variants:
        if variant["compile_status"] != "pending":
            patched_variants.append(variant)
            continue

        print(f"[Patching]   {variant['id']}...", flush=True)
        patched = _patch_single_variant(variant, target_config, max_retries, messages)
        patched_variants.append(patched)

    compiled_count = sum(1 for v in patched_variants if v["compile_status"] == "ok")
    messages.append(f"[Patching] {compiled_count}/{len(patched_variants)} variants compiled successfully")

    return {
        "variants": patched_variants,
        "messages": messages,
    }
