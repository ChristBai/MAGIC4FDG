"""Patching Agent：修复生成的 fuzz driver 中的编译错误。

对每个 compile_status="pending" 的变体：
1. 在 Docker 中尝试编译
2. 编译成功 → 标记 "ok"，进入 coverage 阶段
3. 编译失败 → 将源码 + 错误信息发给 LLM 修复，最多重试 max_compile_retries 次
4. 重试耗尽仍失败 → 标记 "failed"

LLM 温度设为 0.2（修复任务需要确定性，不需要创造性）。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    """渲染修复 prompt：将编译错误和源码填入模板。"""
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
    """尝试编译变体，返回 (是否成功, 错误信息)。"""
    driver_filename = f"patch_{variant['id']}.cpp"
    return compile_driver(variant["source_code"], target_config, driver_filename)


def _patch_single_variant(
    variant: DriverVariant,
    target_config: dict,
    max_retries: int,
    messages: list[str],
) -> DriverVariant:
    """对单个变体执行编译→修复循环。"""
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
    """LangGraph 节点：并行编译所有 pending 变体，失败则调用 LLM 修复。"""
    target_config = state["target_config"]
    variants = list(state.get("variants", []))
    max_retries = state.get("max_compile_retries", 3)
    messages = list(state.get("messages", []))

    pending = [v for v in variants if v["compile_status"] == "pending"]
    non_pending = [v for v in variants if v["compile_status"] != "pending"]
    print(f"[Patching] {len(pending)} pending variants to compile", flush=True)

    max_workers = int(os.environ.get("FUZZFORGE_DOCKER_PARALLEL", os.environ.get("FUZZFORGE_PARALLEL", "5")))

    if not pending:
        compiled_count = sum(1 for v in non_pending if v["compile_status"] == "ok")
        messages.append(f"[Patching] {compiled_count}/{len(non_pending)} variants compiled successfully")
        return {"variants": non_pending, "messages": messages}

    def _patch_one(variant):
        local_msgs: list[str] = []
        patched = _patch_single_variant(variant, target_config, max_retries, local_msgs)
        return patched, local_msgs

    patched_variants: list[DriverVariant] = list(non_pending)

    with ThreadPoolExecutor(max_workers=min(len(pending), max_workers)) as pool:
        futures = {pool.submit(_patch_one, v): v for v in pending}
        for future in as_completed(futures):
            patched, local_msgs = future.result()
            patched_variants.append(patched)
            messages.extend(local_msgs)
            status = "OK" if patched["compile_status"] == "ok" else "FAILED"
            print(f"[Patching]   {patched['id']} → {status}", flush=True)

    compiled_count = sum(1 for v in patched_variants if v["compile_status"] == "ok")
    messages.append(f"[Patching] {compiled_count}/{len(patched_variants)} variants compiled successfully")

    return {
        "variants": patched_variants,
        "messages": messages,
    }
