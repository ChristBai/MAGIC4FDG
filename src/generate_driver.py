#!/usr/bin/env python3
"""Render a fuzz-driver prompt and optionally call a real LLM API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from target_config import (
    REQUIRED_FIELDS_DRIVER,
    ROOT,
    load_target_config,
    resolve_project_path,
)

TEMPLATE_PATH = ROOT / "prompts" / "libfuzzer_driver_prompt.txt"
PROMPT_OUTPUT_PATH = ROOT / "generated" / "prompt.txt"
DRIVER_OUTPUT_PATH = ROOT / "generated" / "fuzz_driver.cpp"
RAW_RESPONSE_PATH = ROOT / "generated" / "llm_response.json"
OPENAI_RESPONSES_URL = "https://ai-api-cn.db-kj.com/v1/responses"


def format_list(values: list[str]) -> str:
    if not values:
        return "(none)"
    return "\n".join(f"- {value}" for value in values)


def render_prompt(target: dict[str, Any]) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    cleanup_function = target.get("cleanup_function") or "(none)"
    return (
        template.replace("{{TARGET_NAME}}", target["target_name"])
        .replace("{{LANGUAGE}}", target["language"])
        .replace("{{FUNCTION_NAME}}", target["function_name"])
        .replace("{{FUNCTION_SIGNATURE}}", target["signature"])
        .replace("{{HEADER}}", target["header"])
        .replace("{{TARGET_DESCRIPTION}}", target["description"])
        .replace("{{CLEANUP_FUNCTION}}", cleanup_function)
        .replace("{{SOURCE_FILES}}", format_list(target["source_files"]))
        .replace("{{INCLUDE_DIRS}}", format_list(target["include_dirs"]))
        .replace("{{SEED_CORPUS}}", target["seed_corpus"])
    )


def extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])

    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("LLM response did not contain output text.")
    return text


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:cpp|c\+\+|cc|cxx|c)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + "\n"


def call_openai_responses(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout_seconds: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API request failed with HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a LibFuzzer driver prompt and optionally call an LLM."
    )
    parser.add_argument(
        "--target-config",
        type=Path,
        required=True,
        help="Path to a JSON target config, for example targets/cjson_parse.json.",
    )
    parser.add_argument(
        "--mode",
        choices=("prompt", "llm"),
        default="prompt",
        help="prompt renders generated/prompt.txt; llm also writes generated/fuzz_driver.cpp.",
    )
    parser.add_argument(
        "--call-llm",
        action="store_true",
        help="Deprecated alias for --mode llm.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
        help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.4.",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable containing the OpenAI API key.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=1200,
        help="Maximum output tokens for generated driver code.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="HTTP timeout for the LLM request.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DRIVER_OUTPUT_PATH,
        help="Where to write generated C++ driver code in --mode llm.",
    )
    args = parser.parse_args()

    target_config_path = resolve_project_path(args.target_config)

    try:
        target = load_target_config(target_config_path, REQUIRED_FIELDS_DRIVER)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    mode = "llm" if args.call_llm else args.mode
    prompt = render_prompt(target)
    PROMPT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_OUTPUT_PATH.write_text(prompt, encoding="utf-8")
    print(prompt)

    if mode == "prompt":
        print(f"\nPrompt saved to {PROMPT_OUTPUT_PATH}")
        print("Dry run only. Use --mode llm to call the OpenAI API.")
        return 0

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(
            f"error: --mode llm requires {args.api_key_env} to be set.",
            file=sys.stderr,
        )
        return 2

    response = call_openai_responses(
        api_key=api_key,
        model=args.model,
        prompt=prompt,
        timeout_seconds=args.timeout_seconds,
        max_output_tokens=args.max_output_tokens,
    )
    RAW_RESPONSE_PATH.write_text(json.dumps(response, indent=2), encoding="utf-8")

    driver_code = strip_code_fences(extract_response_text(response))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(driver_code, encoding="utf-8")

    print(f"\nLLM response saved to {RAW_RESPONSE_PATH}")
    print(f"Generated driver saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
